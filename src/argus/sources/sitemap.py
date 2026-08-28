from __future__ import annotations

import asyncio
import re
import zlib
from urllib.parse import urldefrag, unquote, urljoin, urlsplit
from xml.etree.ElementTree import Element, ParseError

from defusedxml import ElementTree as DefusedET
from defusedxml.common import DefusedXmlException

from argus.config import Settings
from argus.contracts.models import CollectionRequest
from argus.crawler.fast.runtime import FastCrawlerRuntime
from argus.crawler.models import FetchResult
from argus.research.territory_relevance import TerritoryRelevanceEvaluator
from argus.sources.base import SourceResult, SourceTask


class SitemapDiscoveryAdapter:
    """Best-effort robots.txt and Sitemap URL discovery.

    This adapter never creates factual observations. It only emits bounded same-host page
    tasks which must be fetched by their configured factual source adapter before they can
    become evidence. Network/parser failures are intentionally fail-open because sitemap
    discovery is a navigation aid, not requested factual coverage.
    """

    source_id = "site_discovery"
    intents: set[str] = set()

    _GZIP_MEDIA_TYPES = {
        "application/gzip",
        "application/x-gzip",
    }
    _MAX_INDEX_DEPTH = 3
    _SOURCE_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
    _DOWNSTREAM_METADATA_KEYS = (
        "research_goals",
        "research_input_candidates",
        "research_input_candidates_navigation_only",
        "research_input_candidates_are_evidence",
        "research_input_scope",
        "dedicated_source_direct_entry",
        "dedicated_source_navigation",
        "source_policy",
    )

    def __init__(self, settings: Settings, fast: FastCrawlerRuntime) -> None:
        self.settings = settings
        self.fast = fast
        self.territory_relevance = TerritoryRelevanceEvaluator()

    async def discover(self, request: CollectionRequest) -> list[SourceTask]:
        del request
        return []

    async def fetch(self, task: SourceTask) -> FetchResult | None:
        try:
            return await self.fast.fetch(task.url)
        except asyncio.CancelledError:
            raise
        except Exception:
            return None

    async def extract(
        self,
        task: SourceTask,
        fetched: FetchResult | None,
        request: CollectionRequest,
    ) -> SourceResult:
        kind = str(task.metadata.get("site_discovery_kind") or "")
        if kind == "robots":
            return SourceResult(
                observations=[],
                discovered_tasks=self._robots_tasks(task, fetched),
            )
        if kind == "sitemap":
            return SourceResult(
                observations=[],
                discovered_tasks=self._sitemap_tasks(task, fetched, request),
            )
        return SourceResult(observations=[])

    async def normalize(self, result: SourceResult) -> SourceResult:
        return result

    async def health(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "status": "ready",
            "mode": "robots_sitemap",
            "max_urls": self.settings.sitemap_max_urls,
            "max_indexes": self.settings.sitemap_max_indexes,
            "max_index_depth": self._MAX_INDEX_DEPTH,
            "gzip_sitemaps": True,
            "gzip_max_uncompressed_bytes": self.settings.max_response_bytes,
            "dedicated_source_routing": True,
            "territory_transliteration_ranking": True,
        }

    def _robots_tasks(
        self,
        task: SourceTask,
        fetched: FetchResult | None,
    ) -> list[SourceTask]:
        root_host = str(task.metadata.get("root_host") or "").lower().strip(".")
        root_origin = str(task.metadata.get("root_origin") or "").rstrip("/")
        if not root_host or not root_origin:
            return []
        if fetched is not None and fetched.blocked:
            return []

        candidates: list[str] = []
        if fetched is not None:
            for line in fetched.text.splitlines():
                key, separator, value = line.partition(":")
                if separator and key.strip().casefold() == "sitemap":
                    candidate = value.strip()
                    if candidate:
                        candidates.append(urljoin(root_origin + "/", candidate))
        candidates.append(root_origin + "/sitemap.xml")

        tasks: list[SourceTask] = []
        seen: set[str] = set()
        for raw in candidates:
            url = urldefrag(raw)[0]
            if url in seen or not self._same_host_url(url, root_host):
                continue
            seen.add(url)
            tasks.append(
                SourceTask(
                    source_id=self.source_id,
                    goal=task.goal,
                    url=url,
                    depth=task.depth,
                    task_key=f"{self.source_id}:sitemap:{url}",
                    metadata={
                        **task.metadata,
                        "site_discovery_kind": "sitemap",
                        "sitemap_index_depth": 0,
                    },
                )
            )
            if len(tasks) >= self.settings.sitemap_max_indexes:
                break
        return tasks

    def _sitemap_tasks(
        self,
        task: SourceTask,
        fetched: FetchResult | None,
        request: CollectionRequest,
    ) -> list[SourceTask]:
        if fetched is None or fetched.blocked:
            return []
        payload = self._xml_payload(fetched)
        if payload is None:
            return []
        try:
            root = DefusedET.fromstring(payload)
        except (DefusedXmlException, ParseError):
            return []

        root_host = str(task.metadata.get("root_host") or "").lower().strip(".")
        if not root_host:
            return []
        root_name = self._local_name(root)
        if root_name == "sitemapindex":
            return self._index_tasks(task, root, root_host, request)
        if root_name == "urlset":
            return self._page_tasks(task, root, root_host, request)
        return []

    def _xml_payload(self, fetched: FetchResult) -> bytes | str | None:
        body = fetched.body
        if self._is_gzip_payload(fetched):
            if body is None:
                return None
            return self._bounded_gzip_decompress(body)
        if body is not None:
            return body
        return fetched.text

    def _bounded_gzip_decompress(self, body: bytes) -> bytes | None:
        limit = self.settings.max_response_bytes
        try:
            decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
            payload = decompressor.decompress(body, limit + 1)
        except zlib.error:
            return None
        if len(payload) > limit:
            return None
        if decompressor.unconsumed_tail or not decompressor.eof:
            return None
        if decompressor.unused_data and decompressor.unused_data.strip(b"\x00"):
            return None
        return payload

    @classmethod
    def _is_gzip_payload(cls, fetched: FetchResult) -> bool:
        body = fetched.body or b""
        if body.startswith(b"\x1f\x8b"):
            return True
        media_type = str(fetched.content_type or "").split(";", 1)[0].strip().casefold()
        return media_type in cls._GZIP_MEDIA_TYPES

    def _index_tasks(
        self,
        task: SourceTask,
        root: Element,
        root_host: str,
        request: CollectionRequest,
    ) -> list[SourceTask]:
        index_depth = int(task.metadata.get("sitemap_index_depth") or 0)
        max_index_depth = min(
            self._MAX_INDEX_DEPTH,
            max(0, int(request.constraints.max_depth)),
        )
        if index_depth >= max_index_depth:
            return []

        urls: list[str] = []
        seen: set[str] = set()
        for child in list(root):
            if self._local_name(child) != "sitemap":
                continue
            raw = self._direct_loc(child)
            if not raw:
                continue
            url = urldefrag(urljoin(task.url, raw))[0]
            if url in seen or not self._same_host_url(url, root_host):
                continue
            seen.add(url)
            urls.append(url)

        ranked = self._rank_urls(urls, request)
        return [
            SourceTask(
                source_id=self.source_id,
                goal=task.goal,
                url=url,
                depth=task.depth,
                task_key=f"{self.source_id}:sitemap:{url}",
                metadata={
                    **task.metadata,
                    "site_discovery_kind": "sitemap",
                    "sitemap_index_depth": index_depth + 1,
                },
            )
            for url in ranked[: self.settings.sitemap_max_indexes]
        ]

    def _page_tasks(
        self,
        task: SourceTask,
        root: Element,
        root_host: str,
        request: CollectionRequest,
    ) -> list[SourceTask]:
        allowed = {item.lower().strip(".") for item in request.constraints.allowed_domains}
        denied = {item.lower().strip(".") for item in request.constraints.denied_domains}
        urls: list[str] = []
        seen: set[str] = set()
        for child in list(root):
            if self._local_name(child) != "url":
                continue
            raw = self._direct_loc(child)
            if not raw:
                continue
            url = urldefrag(urljoin(task.url, raw))[0]
            if url in seen or not self._same_host_url(url, root_host):
                continue
            host = (urlsplit(url).hostname or "").lower().strip(".")
            if any(host == item or host.endswith("." + item) for item in denied):
                continue
            if allowed and not any(
                host == item or host.endswith("." + item) for item in allowed
            ):
                continue
            seen.add(url)
            urls.append(url)

        ranked = self._rank_urls(urls, request)
        collection_id = str(task.metadata.get("collection_id") or "")
        target_source_id = self._target_source_id(task)
        return [
            SourceTask(
                source_id=target_source_id,
                goal=task.goal,
                url=url,
                depth=task.depth + 1,
                metadata=self._downstream_metadata(
                    task,
                    request,
                    collection_id=collection_id,
                    rank=rank,
                ),
            )
            for rank, url in enumerate(
                ranked[: self.settings.sitemap_max_urls],
                start=1,
            )
        ]

    def _rank_urls(self, urls: list[str], request: CollectionRequest) -> list[str]:
        weighted_tokens: dict[str, int] = {}

        def add_tokens(value: str, weight: int) -> None:
            tokens = self.territory_relevance.territory_tokens(value)
            lexical = [token for token in tokens if not token[:1].isdigit()]
            aliases = self.territory_relevance.latin_address_aliases(lexical)
            for token in [*tokens, *aliases]:
                current = weighted_tokens.get(token, 0)
                weighted_tokens[token] = max(current, weight if not token[:1].isdigit() else 1)

        add_tokens(request.territory.city or "", 4)
        add_tokens(request.territory.address or "", 5)
        for intent in request.intents:
            for token in re.findall(r"[a-z0-9_]{3,}", intent.casefold()):
                weighted_tokens[token] = max(weighted_tokens.get(token, 0), 1)

        def score(item: tuple[int, str]) -> tuple[int, int]:
            index, url = item
            haystack = unquote(urlsplit(url).path + "?" + urlsplit(url).query).casefold()
            url_tokens = set(re.findall(r"[a-zа-яё0-9]+", haystack, flags=re.UNICODE))
            relevance = sum(weight for token, weight in weighted_tokens.items() if token in url_tokens)
            return (-relevance, index)

        return [url for _, url in sorted(enumerate(urls), key=score)]

    def _downstream_metadata(
        self,
        task: SourceTask,
        request: CollectionRequest,
        *,
        collection_id: str,
        rank: int,
    ) -> dict[str, object]:
        inherited_allowed = task.metadata.get("allowed_domains")
        if isinstance(inherited_allowed, list):
            allowed_domains = [str(item) for item in inherited_allowed]
        else:
            allowed_domains = list(request.constraints.allowed_domains)
        metadata: dict[str, object] = {
            "collection_id": collection_id,
            "discovery_provider": "sitemap",
            "discovery_rank": rank,
            "allowed_domains": allowed_domains,
            "disable_site_discovery": True,
            "sitemap_navigation_only": True,
            "sitemap_source_url": task.url,
        }
        for key in self._DOWNSTREAM_METADATA_KEYS:
            if key in task.metadata:
                metadata[key] = task.metadata[key]
        return metadata

    @classmethod
    def _target_source_id(cls, task: SourceTask) -> str:
        raw = str(task.metadata.get("site_discovery_target_source_id") or "generic_web").strip()
        if raw == cls.source_id or cls._SOURCE_ID_RE.fullmatch(raw) is None:
            return "generic_web"
        return raw

    @staticmethod
    def _direct_loc(element: Element) -> str | None:
        for child in list(element):
            if SitemapDiscoveryAdapter._local_name(child) == "loc" and child.text:
                value = child.text.strip()
                return value or None
        return None

    @staticmethod
    def _local_name(element: Element) -> str:
        return str(element.tag).split("}")[-1].casefold()

    @staticmethod
    def _same_host_url(url: str, root_host: str) -> bool:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        if parsed.username or parsed.password:
            return False
        return parsed.hostname.lower().strip(".") == root_host
