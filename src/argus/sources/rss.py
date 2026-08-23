from __future__ import annotations

from datetime import datetime
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

    def __init__(self, fast: FastCrawlerRuntime, snapshots: SnapshotService) -> None:
        self.fast = fast
        self.snapshots = snapshots

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
        try:
            root = DefusedET.fromstring(fetched.text)
        except (DefusedXmlException, ParseError):
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
        if not items:
            items = root.findall(".//{http://www.w3.org/2005/Atom}entry")
        observations: list[Observation] = []
        evidence_items: list[Evidence] = []
        for item in items[:100]:
            title = self._text(item, "title")
            description = (
                self._text(item, "description")
                or self._text(item, "summary")
                or self._text(item, "content")
            )
            entity_id = self._text(item, "guid") or self._text(item, "id")
            link = self._safe_item_link(fetched.final_url, self._link(item))
            raw = "\n".join(x for x in (title, description) if x)
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
                data={},
                published_at=self._date(item),
                content_hash=content_hash,
                provenance={
                    "feed_url": fetched.final_url,
                    "entry_url": link,
                    "snapshot_id": snapshot.snapshot_id,
                },
                quality={"evidence_backed": True},
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
                metadata={"entry_url": link},
            )
            evidence_items.append(evidence)
        return SourceResult(observations=observations, evidence=evidence_items)

    async def normalize(self, result: SourceResult) -> SourceResult:
        return result

    async def health(self) -> dict[str, object]:
        return {"source_id": self.source_id, "status": "ok"}

    @staticmethod
    def _text(item: Element, local_name: str) -> str | None:
        for child in item.iter():
            if child.tag.split("}")[-1] == local_name and child.text:
                return child.text.strip()
        return None

    @staticmethod
    def _link(item: Element) -> str | None:
        for child in item.iter():
            if child.tag.split("}")[-1] == "link":
                if child.text and child.text.strip():
                    return child.text.strip()
                href = child.attrib.get("href")
                if href:
                    return href
        return None

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
        value = (
            cls._text(item, "pubDate")
            or cls._text(item, "published")
            or cls._text(item, "updated")
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
