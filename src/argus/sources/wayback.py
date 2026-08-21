from __future__ import annotations

import json

from argus.contracts.models import CollectionRequest, Evidence, EvidenceSource, Observation
from argus.history.snapshots import SnapshotService, sha256_text
from argus.history.wayback import WaybackCDXProvider, WaybackCapture, WaybackCaptureResult
from argus.normalization.identity import stable_evidence_id, stable_observation_id
from argus.sources.base import SourceResult, SourceTask


class WaybackSourceAdapter:
    """Turn exact-URL CDX captures into evidence and archived-page crawl tasks."""

    source_id = "wayback_cdx"
    intents = {"historical_context"}

    def __init__(self, provider: WaybackCDXProvider, snapshots: SnapshotService) -> None:
        self.provider = provider
        self.snapshots = snapshots

    async def discover(self, request: CollectionRequest) -> list[SourceTask]:
        tasks: list[SourceTask] = []
        for url in request.constraints.seed_urls:
            target = str(url)
            tasks.append(
                SourceTask(
                    source_id=self.source_id,
                    goal="historical_context",
                    url=target,
                    task_key=f"{self.source_id}:{target}",
                    metadata={"archive_target_url": target},
                )
            )
        return tasks

    async def fetch(self, task: SourceTask) -> WaybackCaptureResult:
        return await self.provider.captures(task.url)

    async def extract(
        self,
        task: SourceTask,
        fetched: WaybackCaptureResult,
        request: CollectionRequest,
    ) -> SourceResult:
        collection_id = str(task.metadata.get("collection_id", ""))
        observations: list[Observation] = []
        evidence_items: list[Evidence] = []
        discovered_tasks: list[SourceTask] = []

        for rank, capture in enumerate(fetched.captures, start=1):
            observation, evidence = await self._capture_observation(
                capture,
                collection_id,
                request,
            )
            observations.append(observation)
            evidence_items.append(evidence)
            discovered_tasks.append(
                SourceTask(
                    source_id="generic_web",
                    goal="historical_context",
                    url=capture.capture_url,
                    depth=task.depth,
                    task_key=(
                        f"generic_web:wayback:{capture.timestamp}:"
                        f"{capture.original_url}"
                    ),
                    metadata={
                        "discovery_provider": self.source_id,
                        "discovery_rank": rank,
                        "archive_original_url": capture.original_url,
                        "archive_timestamp": capture.timestamp,
                    },
                )
            )

        errors = [error for error in fetched.errors if error.code != "ARCHIVE_NO_CAPTURES"]
        return SourceResult(
            observations=observations,
            evidence=evidence_items,
            discovered_tasks=discovered_tasks,
            blocked=fetched.blocked,
            partial=bool(errors and fetched.captures),
            errors=errors,
        )

    async def normalize(self, result: SourceResult) -> SourceResult:
        return result

    async def health(self) -> dict[str, object]:
        return await self.provider.health()

    async def _capture_observation(
        self,
        capture: WaybackCapture,
        collection_id: str,
        request: CollectionRequest,
    ) -> tuple[Observation, Evidence]:
        facts = {
            "timestamp": capture.timestamp,
            "original_url": capture.original_url,
            "capture_url": capture.capture_url,
            "mimetype": capture.mimetype,
            "status_code": capture.status_code,
            "digest": capture.digest,
            "length": capture.length,
        }
        canonical = json.dumps(
            facts,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        content_hash = sha256_text(canonical)
        snapshot = await self.snapshots.capture(
            self.source_id,
            capture.capture_url,
            canonical,
            "application/json",
        )
        entity_id = f"{capture.timestamp}:{capture.original_url}"
        observation_id = stable_observation_id(
            collection_id=collection_id,
            source_id=self.source_id,
            entity_type="archive_capture",
            entity_id=entity_id,
            source_url=capture.capture_url,
            content_hash=content_hash,
        )
        provenance = {
            "snapshot_id": snapshot.snapshot_id,
            "archive_provider": self.source_id,
            "original_url": capture.original_url,
            "capture_timestamp": capture.timestamp,
        }
        observation = Observation(
            observation_id=observation_id,
            collection_id=collection_id,
            analysis_id=request.analysis_id,
            consumer=request.consumer,
            source=self.source_id,
            source_kind="archive_capture_index",
            url=capture.capture_url,
            entity_type="archive_capture",
            entity_id=entity_id,
            title=f"Archived capture: {capture.original_url}",
            text=(
                f"Wayback capture {capture.timestamp} for {capture.original_url}"
            ),
            data=facts,
            published_at=capture.captured_at,
            content_hash=content_hash,
            provenance=provenance,
            quality={"evidence_backed": True, "archive_index": True},
        )
        evidence_text = canonical[:10_000]
        evidence = Evidence(
            evidence_id=stable_evidence_id(
                observation_id=observation.observation_id,
                evidence_type="archive_capture_index",
                source_url=capture.capture_url,
                text=evidence_text,
            ),
            observation_id=observation.observation_id,
            type="archive_capture_index",
            text=evidence_text,
            source=EvidenceSource(
                provider=self.source_id,
                url=capture.capture_url,
                collected_at=observation.collected_at,
                source_id=self.source_id,
            ),
            metadata={"provenance": provenance},
        )
        return observation, evidence
