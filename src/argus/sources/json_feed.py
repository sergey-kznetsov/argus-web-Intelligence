from __future__ import annotations

import json
from datetime import datetime
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from argus.contracts.models import (
    CollectionRequest,
    Evidence,
    EvidenceSource,
    Observation,
    StructuredError,
)
from argus.crawler.fast.runtime import FastCrawlerRuntime
from argus.extraction.structured_data import BoundedStructuredDataExtractor
from argus.history.snapshots import SnapshotService, sha256_text
from argus.normalization.identity import stable_evidence_id, stable_observation_id
from argus.sources.base import SourceResult, SourceTask


class JSONFeedAdapter:
    """Evidence-first adapter for public JSON Feed 1.0/1.1 documents."""

    source_id = "json_feed"
    intents = {
        "local_news",
        "public_mentions",
        "incidents",
        "discussions",
        "historical_context",
    }
    _SUPPORTED_VERSIONS = {
        "https://jsonfeed.org/version/1",
        "https://jsonfeed.org/version/1.1",
    }

    def __init__(
        self,
        fast: FastCrawlerRuntime,
        snapshots: SnapshotService,
        structured_extractor: BoundedStructuredDataExtractor,
        *,
        max_items: int = 100,
    ) -> None:
        self.fast = fast
        self.snapshots = snapshots
        self.structured_extractor = structured_extractor
        self.max_items = max(1, min(int(max_items), structured_extractor.max_records, 1000))

    async def discover(self, request: CollectionRequest) -> list[SourceTask]:
        tasks: list[SourceTask] = []
        for value in request.constraints.seed_urls:
            url = str(value)
            path = urlsplit(url).path.casefold().rstrip("/")
            if path.endswith(("/feed.json", ".feed.json", ".jsonfeed")):
                tasks.append(
                    SourceTask(
                        source_id=self.source_id,
                        goal=request.intents[0],
                        url=url,
                        metadata={"research_goals": list(request.intents)},
                    )
                )
        return tasks

    async def fetch(self, task: SourceTask):
        return await self.fast.fetch(task.url)

    async def extract(self, task: SourceTask, fetched, request: CollectionRequest) -> SourceResult:
        if fetched.blocked:
            return SourceResult(observations=[], blocked=True)

        body = fetched.body if fetched.body is not None else fetched.text.encode("utf-8")
        extraction = self.structured_extractor.extract(
            body,
            content_type=fetched.content_type,
            url=fetched.final_url,
        )
        if extraction.error_code is not None or extraction.document_type != "json":
            return SourceResult(
                observations=[],
                errors=[
                    StructuredError(
                        code=extraction.error_code or "JSON_FEED_INVALID",
                        message=extraction.error_message or "JSON Feed is not valid JSON",
                        retryable=False,
                        source_id=self.source_id,
                    )
                ],
            )

        payload = extraction.payload
        semantic_error = self._validate_feed(payload)
        if semantic_error is not None:
            return SourceResult(
                observations=[],
                errors=[
                    StructuredError(
                        code="JSON_FEED_INVALID",
                        message=semantic_error,
                        retryable=False,
                        source_id=self.source_id,
                    )
                ],
            )
        assert isinstance(payload, dict)
        version = str(payload["version"])
        title = str(payload["title"])
        items = payload["items"]
        assert isinstance(items, list)

        collection_id = str(task.metadata.get("collection_id", ""))
        snapshot = await self.snapshots.capture(
            self.source_id,
            fetched.final_url,
            fetched.text,
            fetched.content_type,
            collection_id=collection_id,
        )
        research_goals = self._research_goals(task)
        observations: list[Observation] = []
        evidence_items: list[Evidence] = []
        invalid_items = 0

        for item in items[: self.max_items]:
            normalized = self._item(item, fetched.final_url)
            if normalized is None:
                invalid_items += 1
                continue
            entity_id, item_url, text, item_payload = normalized
            canonical = json.dumps(
                item_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            content_hash = sha256_text(canonical)
            observation_id = stable_observation_id(
                collection_id=collection_id,
                source_id=self.source_id,
                entity_type="publication",
                source_url=item_url,
                content_hash=content_hash,
                entity_id=entity_id,
            )
            observation = Observation(
                observation_id=observation_id,
                collection_id=collection_id,
                analysis_id=request.analysis_id,
                consumer=request.consumer,
                source=self.source_id,
                source_kind="json_feed_item",
                url=item_url,
                entity_type="publication",
                entity_id=entity_id,
                title=self._string(item_payload.get("title"), 1_000),
                text=text[:100_000],
                data={
                    "feed_title": title,
                    "feed_version": version,
                    "item": item_payload,
                },
                published_at=self._date(item_payload.get("date_published")),
                content_hash=content_hash,
                provenance={
                    "feed_url": fetched.final_url,
                    "item_url": item_url,
                    "snapshot_id": snapshot.snapshot_id,
                    "json_feed_version": version,
                    "research_goals": research_goals,
                    "structured_extractor": extraction.extractor_version,
                },
                quality={"evidence_backed": True, "machine_readable": True},
            )
            evidence_text = canonical[:10_000]
            evidence = Evidence(
                evidence_id=stable_evidence_id(
                    observation_id=observation.observation_id,
                    evidence_type="json_feed_item",
                    source_url=fetched.final_url,
                    text=evidence_text,
                ),
                observation_id=observation.observation_id,
                type="json_feed_item",
                text=evidence_text,
                source=EvidenceSource(
                    provider=self.source_id,
                    url=fetched.final_url,
                    collected_at=observation.collected_at,
                    source_id=self.source_id,
                ),
                metadata={
                    "item_id": entity_id,
                    "item_url": item_url,
                    "feed_version": version,
                    "research_goals": research_goals,
                },
            )
            observations.append(observation)
            evidence_items.append(evidence)

        truncated = len(items) > self.max_items
        errors: list[StructuredError] = []
        if invalid_items:
            errors.append(
                StructuredError(
                    code="JSON_FEED_ITEM_INVALID",
                    message=f"JSON Feed contained {invalid_items} invalid item(s) that were skipped",
                    retryable=False,
                    source_id=self.source_id,
                )
            )
        if truncated:
            errors.append(
                StructuredError(
                    code="JSON_FEED_ITEM_LIMIT",
                    message=f"JSON Feed item limit reached ({self.max_items})",
                    retryable=False,
                    source_id=self.source_id,
                )
            )
        return SourceResult(
            observations=observations,
            evidence=evidence_items,
            partial=bool(errors),
            errors=errors,
        )

    async def normalize(self, result: SourceResult) -> SourceResult:
        return result

    async def health(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "status": "ok",
            "versions": sorted(self._SUPPORTED_VERSIONS),
            "max_items": self.max_items,
            "network_during_parse": False,
        }

    @classmethod
    def _validate_feed(cls, payload: object) -> str | None:
        if not isinstance(payload, dict):
            return "JSON Feed root must be an object"
        version = payload.get("version")
        if not isinstance(version, str) or version not in cls._SUPPORTED_VERSIONS:
            return "JSON Feed version is missing or unsupported"
        title = payload.get("title")
        if not isinstance(title, str) or not title.strip():
            return "JSON Feed title must be a non-empty string"
        if not isinstance(payload.get("items"), list):
            return "JSON Feed items must be an array"
        return None

    @classmethod
    def _item(
        cls,
        raw: object,
        feed_url: str,
    ) -> tuple[str, str, str, dict[str, object]] | None:
        if not isinstance(raw, dict):
            return None
        raw_id = raw.get("id")
        if isinstance(raw_id, bool) or not isinstance(raw_id, (str, int, float)):
            return None
        entity_id = str(raw_id).strip()
        if not entity_id:
            return None

        content_text = cls._string(raw.get("content_text"), 100_000)
        content_html = cls._string(raw.get("content_html"), 250_000)
        if content_text is None and content_html is None:
            return None
        text = content_text or cls._html_text(content_html or "")
        if not text.strip():
            return None

        item_payload = {str(key): value for key, value in raw.items()}
        item_url = cls._safe_url(feed_url, raw.get("url"))
        return entity_id, item_url, text, item_payload

    @staticmethod
    def _string(value: object, limit: int) -> str | None:
        if not isinstance(value, str):
            return None
        text = value.strip()
        return text[:limit] if text else None

    @staticmethod
    def _html_text(value: str) -> str:
        soup = BeautifulSoup(value, "html.parser")
        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()
        return "\n".join(
            line.strip() for line in soup.get_text("\n").splitlines() if line.strip()
        )[:100_000]

    @staticmethod
    def _safe_url(feed_url: str, raw: object) -> str:
        if not isinstance(raw, str) or not raw.strip():
            return feed_url
        candidate = urljoin(feed_url, raw.strip())
        parsed = urlsplit(candidate)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return feed_url
        if parsed.username or parsed.password:
            return feed_url
        return candidate

    @staticmethod
    def _date(value: object) -> datetime | None:
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None

    @staticmethod
    def _research_goals(task: SourceTask) -> list[str]:
        raw = task.metadata.get("research_goals", [])
        goals: list[str] = []
        if isinstance(raw, list):
            for value in raw:
                goal = str(value).strip()
                if goal and goal not in goals:
                    goals.append(goal)
        if not goals and task.goal:
            goals.append(task.goal)
        return goals
