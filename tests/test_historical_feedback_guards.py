from __future__ import annotations

from pathlib import Path

import pytest

from argus.contracts.models import CollectionRequest, Observation
from argus.history.snapshots import SnapshotService
from argus.history.wayback import WaybackCapture, WaybackCaptureResult
from argus.research.historical import HistoricalBranchPlanner
from argus.sources.base import SourceTask
from argus.sources.wayback import WaybackSourceAdapter
from argus.storage.sqlite import SQLiteRepository


class ProviderStub:
    async def captures(self, url):
        del url
        raise AssertionError("fetch is not used")

    async def health(self):
        return {"status": "ok"}


def request() -> CollectionRequest:
    return CollectionRequest(
        consumer="historical-feedback-test",
        analysis_id="historical-feedback-analysis",
        territory={"city": "Ижевск"},
        intents=["historical_context"],
        constraints={"max_pages": 20},
    )


def derived(source_kind: str) -> Observation:
    return Observation(
        observation_id=f"{source_kind}-1",
        collection_id="historical-feedback",
        analysis_id="historical-feedback-analysis",
        consumer="historical-feedback-test",
        source="generic_web",
        source_kind=source_kind,
        url="https://example.com/history",
        entity_type=source_kind,
        title="Derived Historical Label",
        data={
            "name": "Derived Historical Label",
            "operator": "Derived Operator",
            "brand": "Derived Brand",
        },
        content_hash="a" * 64,
    )


def test_derived_historical_rows_cannot_seed_new_historical_queries():
    planner = HistoricalBranchPlanner()

    queries = planner.expand(
        request(),
        [
            derived("archive_capture_index"),
            derived("historical_page_version"),
            derived("historical_entity_change"),
        ],
        seen_queries=set(),
    )

    assert queries == []


@pytest.mark.asyncio
async def test_wayback_adapter_emits_capture_tasks_oldest_first(tmp_path: Path):
    repository = SQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    adapter = WaybackSourceAdapter(ProviderStub(), SnapshotService(repository))
    target = "https://example.com/project"
    captures = [
        WaybackCapture(
            timestamp="20250101000000",
            original_url=target,
            capture_url=f"https://web.archive.org/web/20250101000000id_/{target}",
        ),
        WaybackCapture(
            timestamp="20230101000000",
            original_url=target,
            capture_url=f"https://web.archive.org/web/20230101000000id_/{target}",
        ),
        WaybackCapture(
            timestamp="20240101000000",
            original_url=target,
            capture_url=f"https://web.archive.org/web/20240101000000id_/{target}",
        ),
    ]
    task = SourceTask(
        source_id="wayback_cdx",
        goal="historical_context",
        url=target,
        metadata={"collection_id": "wayback-order"},
    )

    result = await adapter.extract(task, WaybackCaptureResult(captures=captures), request())

    assert [item.metadata["archive_timestamp"] for item in result.discovered_tasks] == [
        "20230101000000",
        "20240101000000",
        "20250101000000",
    ]
    assert [item.metadata["discovery_rank"] for item in result.discovered_tasks] == [1, 2, 3]
    assert [item.data["timestamp"] for item in result.observations] == [
        "20230101000000",
        "20240101000000",
        "20250101000000",
    ]
    await repository.close()
