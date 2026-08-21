from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime

from argus.contracts.models import CollectionRequest, Evidence, EvidenceSource, Observation
from argus.crawler.fast.runtime import FastCrawlerRuntime
from argus.history.snapshots import SnapshotService, sha256_text
from argus.sources.base import SourceResult, SourceTask


class RSSAdapter:
    source_id = "rss_atom"
    intents = {"local_news", "public_mentions", "incidents", "discussions", "historical_context"}

    def __init__(self, fast: FastCrawlerRuntime, snapshots: SnapshotService) -> None:
        self.fast = fast
        self.snapshots = snapshots

    async def discover(self, request: CollectionRequest) -> list[SourceTask]:
        return [SourceTask(source_id=self.source_id, goal=request.intents[0], url=str(url))
                for url in request.constraints.seed_urls
                if str(url).lower().endswith((".rss", ".xml", ".atom"))]

    async def fetch(self, task: SourceTask):
        return await self.fast.fetch(task.url)

    async def extract(self, task: SourceTask, fetched, request: CollectionRequest) -> SourceResult:
        if fetched.blocked:
            return SourceResult(observations=[], blocked=True)
        await self.snapshots.capture(self.source_id, fetched.final_url, fetched.text, fetched.content_type)
        root = ET.fromstring(fetched.text)
        items = root.findall(".//item")
        if not items:
            items = root.findall(".//{http://www.w3.org/2005/Atom}entry")
        observations: list[Observation] = []
        evidence_items: list[Evidence] = []
        for item in items[:100]:
            title = self._text(item, "title")
            description = self._text(item, "description") or self._text(item, "summary") or self._text(item, "content")
            link = self._link(item) or fetched.final_url
            raw = "\n".join(x for x in (title, description) if x)
            observation = Observation(
                collection_id=str(task.metadata.get("collection_id", "")),
                analysis_id=request.analysis_id,
                consumer=request.consumer,
                source=self.source_id,
                source_kind="feed_entry",
                url=link,
                entity_type="publication",
                title=title,
                text=description,
                data={},
                published_at=self._date(item),
                content_hash=sha256_text(raw),
                provenance={"feed_url": fetched.final_url},
                quality={"evidence_backed": True},
            )
            observations.append(observation)
            evidence = Evidence(
                observation_id=observation.observation_id,
                type="feed_entry",
                text=raw[:10_000],
                source=EvidenceSource(provider=self.source_id, url=link,
                                      collected_at=observation.collected_at, source_id=self.source_id),
            )
            evidence_items.append(evidence)
        return SourceResult(observations=observations, evidence=evidence_items)

    async def normalize(self, result: SourceResult) -> SourceResult:
        return result

    async def health(self) -> dict[str, object]:
        return {"source_id": self.source_id, "status": "ok"}

    @staticmethod
    def _text(item: ET.Element, local_name: str) -> str | None:
        for child in item.iter():
            if child.tag.split("}")[-1] == local_name and child.text:
                return child.text.strip()
        return None

    @staticmethod
    def _link(item: ET.Element) -> str | None:
        for child in item.iter():
            if child.tag.split("}")[-1] == "link":
                if child.text and child.text.strip():
                    return child.text.strip()
                href = child.attrib.get("href")
                if href:
                    return href
        return None

    @classmethod
    def _date(cls, item: ET.Element) -> datetime | None:
        value = cls._text(item, "pubDate") or cls._text(item, "published") or cls._text(item, "updated")
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
