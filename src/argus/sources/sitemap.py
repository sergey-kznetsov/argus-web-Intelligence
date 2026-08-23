from __future__ import annotations

import asyncio
import re
from urllib.parse import urldefrag, unquote, urljoin, urlsplit
from xml.etree.ElementTree import Element, ParseError

from defusedxml import ElementTree as DefusedET
from defusedxml.common import DefusedXmlException

from argus.config import Settings
from argus.contracts.models import CollectionRequest
from argus.crawler.fast.runtime import FastCrawlerRuntime
from argus.crawler.models import FetchResult
from argus.sources.base import SourceResult, SourceTask


class SitemapDiscoveryAdapter:
    """Best-effort robots.txt and Sitemap URL discovery.

    This adapter never creates factual observations. It only emits bounded same-host
    Generic Web tasks which must be fetched normally before they can become evidence.
    Network/parser failures are intentionally fail-open because sitemap discovery is
    an optional navigation aid, not requested factual coverage.
    """

    source_id = "site_discovery"
    intents: set[str] = set()

    def __init__(self, settings: Settings, fast: FastCrawlerRuntime) -> None:
        self.settings = settings
        self.fast = fast

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
            if url in seen or not self._same_host_url(url, root_host) or self._is_gzip(url):
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
        try:
            root = DefusedET.fromstring(fetched.text)
        except (DefusedXmlException, ParseError):
            return []

        root_host = str(task.metadata.get("root_host") or "").lower().strip(".")
        if not root_host:
            return []
        root_name = self._local_name(root)
        if root_name == "sitemapindex":
            return self._index_tasks(task, root, root_host)
        if root_name == "urlset":
            return self._page_tasks(task, root, root_host, request)
        return []

    def _index_tasks(
        self,
        task: SourceTask,
        root: Element,
        root_host: str,
    ) -> list[SourceTask]:
        index_depth = int(task.metadata.get("sitemap_index_depth") or 0)
        if index_depth >= 1:
            return []
        tasks: list[SourceTask] = []
        seen: set[str] = set()
        for child in list(root):
            if self._local_name(child) != "sitemap":
                continue
            raw = self._direct_loc(child)
            if not raw:
                continue
            url = urldefrag(urljoin(task.url, raw))[0]
            if url in seen or not self._same_host_url(url, root_host) or self._is_gzip(url):
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
                        "sitemap_index_depth": index_depth + 1,
                    },
                )
            )
            if len(tasks) >= self.settings.sitemap_max_indexes:
                break
        return tasks

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
        return [
            SourceTask(
                source_id="generic_web",
                goal=task.goal,
                url=url,
                depth=task.depth + 1,
                metadata={
                    "collection_id": collection_id,
                    "discovery_provider": "sitemap",
                    "discovery_rank": rank,
                    "allowed_domains": list(request.constraints.allowed_domains),
                    "disable_site_discovery": True,
                },
            )
            for rank, url in enumerate(
                ranked[: self.settings.sitemap_max_urls],
                start=1,
            )
        ]

    @classmethod
    def _rank_urls(cls, urls: list[str], request: CollectionRequest) -> list[str]:
        hints = [
            request.territory.city or "",
            request.territory.address or "",
            *request.intents,
        ]
        tokens: set[str] = set()
        for hint in hints:
            tokens.update(
                token.casefold()
                for token in re.findall(r"\w+", hint, flags=re.UNICODE)
                if len(token) >= 3
            )

        def score(item: tuple[int, str]) -> tuple[int, int]:
            index, url = item
            haystack = unquote(urlsplit(url).path + "?" + urlsplit(url).query).casefold()
            matches = sum(1 for token in tokens if token in haystack)
            return (-matches, index)

        return [url for _, url in sorted(enumerate(urls), key=score)]

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

    @staticmethod
    def _is_gzip(url: str) -> bool:
        return urlsplit(url).path.casefold().endswith(".gz")
