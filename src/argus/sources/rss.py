from __future__ import annotations

from datetime import datetime
import io
from urllib.parse import urljoin, urlsplit
from xml.etree.ElementTree import Element, ParseError

from defusedxml import ElementTree as DefusedET
from defusedxml.common import DefusedXmlException

from argus.contracts.models import (
    CollectionRequest,
    Evidence,
    EvidenceSource,
    Observation,
    StructuredError,
)
from argus.crawler.fast.runtime import FastCrawlerRuntime
from argus.history.snapshots import SnapshotService, sha256_text
from argus.normalization.identity import stable_evidence_id, stable_observation_id
from argus.sources.base import SourceResult, SourceTask


class RSSAdapter:
    source_id = "rss_atom"
    intents = {
        "local_news",
        "public_mentions",
        "incidents",
        "discussions",
        "historical_context",
    }

    def __init__(
        self,
        fast: FastCrawlerRuntime,
        snapshots: SnapshotService,
        *,
        max_items: int = 100,
        max_xml_nodes: int = 20_000,
        max_xml_depth: int = 32,
        max_title_chars: int = 1_000,
        max_entry_text_chars: int = 100_000,
        max_identifier_chars: int = 2_000,
    ) -> None:
        self.fast = fast
        self.snapshots = snapshots
        self.max_items = max(1, int(max_items))
        self.max_xml_nodes = max(1, int(max_xml_nodes))
        self.max_xml_depth = max(1, int(max_xml_depth))
        self.max_title_chars = max(1, int(max_title_chars))
        self.max_entry_text_chars = max(1, int(max_entry_text_chars))
        self.max_identifier_chars = max(1, int(max_identifier_chars))

    async def discover(self, request: CollectionRequest) -> list[SourceTask]:
        return [
            SourceTask(source_id=self.source_id, goal=request.intents[0], url=str(url))
            for url in request.constraints.seed_urls
            if str(url).lower().endswith((".rss", ".xml", ".atom"))
        ]

    async def fetch(self, task: SourceTask):
        return await self.fast.fetch(task.url)

    async def extract(self, task: SourceTask, fetched, request: CollectionRequest) -> SourceResult:
        if fetched.blocked:
            return SourceResult(observations=[], blocked=True)
        collection_id = str(task.metadata.get("collection_id", ""))
        snapshot = await self.snapshots.capture(
            self.source_id,
            fetched.final_url,
            fetched.text,
            fetched.content_type,
            collection_id=collection_id,
        )

        preflight, node_count, max_depth = self._xml_preflight(fetched.text)
        if preflight == "invalid":
            return SourceResult(
                observations=[],
                errors=[
                    StructuredError(
                        code="FEED_XML_INVALID",
                        message="RSS/Atom XML could not be parsed safely",
                        retryable=False,
                        source_id=self.source_id,
                    )
                ],
            )
        if preflight == "limit":
            return SourceResult(
                observations=[],
                partial=True,
                errors=[
                    StructuredError(
                        code="FEED_XML_LIMIT_EXCEEDED",
                        message=(
                            "RSS/Atom XML exceeds configured node or depth limits"
                        ),
                        retryable=False,
                        source_id=self.source_id,
                    )
                ],
            )

        try:
            root = DefusedET.fromstring(fetched.text)
        except (DefusedXmlException, ParseError, ValueError, RecursionError):
            return SourceResult(
                observations=[],
                errors=[
                    StructuredError(
                        code="FEED_XML_INVALID",
                        message="RSS/Atom XML could not be parsed safely",
                        retryable=False,
                        source_id=self.source_id,
                    )
                ],
            )

        items = root.findall(".//item")
        feed_format = "rss"
        if not items:
            items = root.findall(".//{http://www.w3.org/2005/Atom}entry")
            feed_format = "atom"
        total_items = len(items)
        observations: list[Observation] = []
        evidence_items: list[Evidence] = []
        output_truncated = total_items > self.max_items

        for index, item in enumerate(items[: self.max_items]):
            title, title_truncated = self._text(item, "title", self.max_title_chars)
            description, description_truncated = self._first_text(
                item,
                ("description", "summary", "content"),
                self.max_entry_text_chars,
            )
            entity_id, entity_truncated = self._first_text(
                item,
                ("guid", "id"),
                self.max_identifier_chars,
            )
            link_raw, link_truncated = self._link(item, self.max_identifier_chars)
            link = self._safe_item_link(fetched.final_url, link_raw)
            entry_truncated = any(
                (
                    title_truncated,
                    description_truncated,
                    entity_truncated,
                    link_truncated,
                )
            )
            output_truncated = output_truncated or entry_truncated
            raw = "\n".join(value for value in (title, description) if value)
            content_hash = sha256_text(raw)
            observation_id = stable_observation_id(
                collection_id=collection_id,
                source_id=self.source_id,
                entity_type="publication",
                source_url=link,
                content_hash=content_hash,
                entity_id=entity_id,
            )
            observation = Observation(
                observation_id=observation_id,
                collection_id=collection_id,
                analysis_id=request.analysis_id,
                consumer=request.consumer,
                source=self.source_id,
                source_kind="feed_entry",
                url=link,
                entity_type="publication",
                entity_id=entity_id,
                title=title,
                text=description,
                data={
                    "feed_format": feed_format,
                    "entry_index": index,
                    "feed_entry_count": total_items,
                    "entry_truncated": entry_truncated,
                },
                published_at=self._date(item),
                content_hash=content_hash,
                provenance={
                    "feed_url": fetched.final_url,
                    "entry_url": link,
                    "snapshot_id": snapshot.snapshot_id,
                    "feed_format": feed_format,
                    "xml_node_count": node_count,
                    "xml_max_depth": max_depth,
                },
                quality={
                    "evidence_backed": True,
                    "machine_readable": True,
                    "lossless": not entry_truncated,
                },
            )
            observations.append(observation)
            evidence_text = raw[:10_000]
            evidence = Evidence(
                evidence_id=stable_evidence_id(
                    observation_id=observation.observation_id,
                    evidence_type="feed_entry",
                    source_url=fetched.final_url,
                    text=evidence_text,
                ),
                observation_id=observation.observation_id,
                type="feed_entry",
                text=evidence_text,
                source=EvidenceSource(
                    provider=self.source_id,
                    url=fetched.final_url,
                    collected_at=observation.collected_at,
                    source_id=self.source_id,
                ),
                metadata={
                    "entry_url": link,
                    "entry_index": index,
                    "feed_format": feed_format,
                    "snapshot_id": snapshot.snapshot_id,
                    "entry_truncated": entry_truncated,
                    "evidence_excerpt_truncated": len(raw) > len(evidence_text),
                },
            )
            evidence_items.append(evidence)

        errors: list[StructuredError] = []
        if output_truncated:
            errors.append(
                StructuredError(
                    code="FEED_EXTRACTION_TRUNCATED",
                    message=(
                        "RSS/Atom extraction reached a configured item or field limit"
                    ),
                    retryable=False,
                    source_id=self.source_id,
                )
            )
        return SourceResult(
            observations=observations,
            evidence=evidence_items,
            partial=output_truncated,
            errors=errors,
        )

    async def normalize(self, result: SourceResult) -> SourceResult:
        return result

    async def health(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "status": "ok",
            "max_items": self.max_items,
            "max_xml_nodes": self.max_xml_nodes,
            "max_xml_depth": self.max_xml_depth,
        }

    def _xml_preflight(self, text: str) -> tuple[str, int, int]:
        nodes = 0
        depth = 0
        maximum_depth = 0
        try:
            for event, element in DefusedET.iterparse(
                io.StringIO(text),
                events=("start", "end"),
            ):
                if event == "start":
                    nodes += 1
                    maximum_depth = max(maximum_depth, depth)
                    if nodes > self.max_xml_nodes or depth > self.max_xml_depth:
                        return "limit", nodes, maximum_depth
                    depth += 1
                    continue
                depth = max(0, depth - 1)
                element.clear()
        except (DefusedXmlException, ParseError, ValueError, RecursionError):
            return "invalid", nodes, maximum_depth
        return "ok", nodes, maximum_depth

    @staticmethod
    def _text(
        item: Element,
        local_name: str,
        limit: int,
    ) -> tuple[str | None, bool]:
        for child in item.iter():
            if child.tag.split("}")[-1] != local_name or not child.text:
                continue
            clean = " ".join(child.text.split()).strip()
            if not clean:
                continue
            return clean[:limit], len(clean) > limit
        return None, False

    @classmethod
    def _first_text(
        cls,
        item: Element,
        local_names: tuple[str, ...],
        limit: int,
    ) -> tuple[str | None, bool]:
        for name in local_names:
            value, truncated = cls._text(item, name, limit)
            if value is not None:
                return value, truncated
        return None, False

    @staticmethod
    def _link(item: Element, limit: int) -> tuple[str | None, bool]:
        fallback: tuple[str | None, bool] = (None, False)
        for child in item.iter():
            if child.tag.split("}")[-1] != "link":
                continue
            raw = None
            if child.text and child.text.strip():
                raw = child.text.strip()
            elif child.attrib.get("href"):
                raw = str(child.attrib["href"]).strip()
            if not raw:
                continue
            bounded = raw[:limit]
            value = (bounded, len(raw) > limit)
            rel = str(child.attrib.get("rel", "")).strip().casefold()
            if rel in {"", "alternate"}:
                return value
            if fallback[0] is None:
                fallback = value
        return fallback

    @staticmethod
    def _safe_item_link(feed_url: str, raw_link: str | None) -> str:
        candidate = urljoin(feed_url, raw_link or feed_url)
        parsed = urlsplit(candidate)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return feed_url
        if parsed.username or parsed.password:
            return feed_url
        return candidate

    @classmethod
    def _date(cls, item: Element) -> datetime | None:
        value, _ = cls._first_text(
            item,
            ("pubDate", "published", "updated"),
            256,
        )
        if not value:
            return None
        try:
            from email.utils import parsedate_to_datetime

            return parsedate_to_datetime(value)
        except (TypeError, ValueError):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
