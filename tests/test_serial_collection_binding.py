from __future__ import annotations

from datetime import UTC, datetime

import pytest

from argus.contracts.models import (
    CollectionRecord,
    CollectionRequest,
    CollectionStatus,
    Observation,
)
from argus.orchestrator.toolpack_aware import (
    ToolPackAwareEvidenceStatusAdaptiveResearchOrchestrator,
)
from argus.sources.base import SourceResult, SourceTask


class FakeRepository:
    async def update_collection(self, record) -> None:
        del record


class FakeAdapter:
    async def fetch(self, task: SourceTask):
        return task.url

    async def extract(self, task: SourceTask, fetched, request: CollectionRequest) -> SourceResult:
        del fetched
        collection_id = str(task.metadata.get("collection_id") or "")
        return SourceResult(
            observations=[
                Observation(
                    collection_id=collection_id,
                    analysis_id=request.analysis_id,
                    consumer=request.consumer,
                    source="fake",
                    source_kind="web_page",
                    url=task.url,
                    entity_type="document",
                    text="serial lane collection binding regression",
                    content_hash="serial-lane-binding",
                )
            ]
        )

    async def normalize(self, result: SourceResult) -> SourceResult:
        return result


class FakeRegistry:
    def __init__(self) -> None:
        self.adapter = FakeAdapter()

    def get(self, source_id: str) -> FakeAdapter:
        assert source_id == "fake"
        return self.adapter


class SerialBindingHarness(ToolPackAwareEvidenceStatusAdaptiveResearchOrchestrator):
    def __init__(self) -> None:
        self.repository = FakeRepository()
        self.registry = FakeRegistry()
        self.source_task_timeout_seconds = 5.0
        self.committed: list[Observation] = []

    async def _is_cancelled(self, collection_id: str) -> bool:
        del collection_id
        return False

    async def _ensure_execution_budget_started(self, record):
        del record
        return datetime.now(UTC)

    @staticmethod
    def _remaining_execution_seconds(started_at, total_seconds: float) -> float:
        del started_at
        return total_seconds

    async def _commit_task_success(
        self,
        record,
        *,
        observations,
        evidence,
        snapshots,
    ) -> None:
        del evidence, snapshots
        assert observations
        assert all(item.collection_id == record.collection_id for item in observations)
        self.committed = list(observations)


def collection_record() -> CollectionRecord:
    request = CollectionRequest(
        consumer="serial.binding.test",
        analysis_id="serial-binding-analysis",
        territory={"city": "Ижевск"},
        intents=["comments"],
        constraints={
            "max_pages": 3,
            "max_duration_seconds": 30,
        },
    )
    timestamp = datetime.now(UTC)
    return CollectionRecord(
        collection_id="collection-current",
        request=request,
        status=CollectionStatus.RUNNING,
        created_at=timestamp,
        updated_at=timestamp,
    )


@pytest.mark.asyncio
async def test_serial_lane_binds_missing_task_collection_before_extract() -> None:
    harness = SerialBindingHarness()
    record = collection_record()
    task = SourceTask(
        source_id="fake",
        goal="comments",
        url="https://example.test/public-map",
    )

    latest, stats = await harness._process_serial_lane(
        record,
        [task],
        [],
        lane_id="public_map:2gis_web",
        lane_kind="public_map",
        lane_label="2gis_web",
        page_limit=1,
        future_lane_count=0,
    )

    assert task.metadata["collection_id"] == record.collection_id
    assert harness.committed[0].collection_id == record.collection_id
    assert stats["processed_pages"] == 1
    assert len(latest.coverage) == 1


@pytest.mark.asyncio
async def test_serial_lane_rejects_conflicting_task_collection() -> None:
    harness = SerialBindingHarness()
    record = collection_record()
    task = SourceTask(
        source_id="fake",
        goal="comments",
        url="https://example.test/conflicting",
        metadata={"collection_id": "collection-other"},
    )

    with pytest.raises(ValueError, match="serial task collection_id does not match collection"):
        await harness._process_serial_lane(
            record,
            [task],
            [],
            lane_id="source_contour:general_web",
            lane_kind="source_contour",
            lane_label="general_web",
            page_limit=1,
            future_lane_count=0,
        )
