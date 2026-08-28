from pathlib import Path

import pytest

from argus.contracts.models import CollectionRequest
from argus.history.snapshots import SnapshotService
from argus.history.wayback import WaybackCapture, WaybackCaptureResult
from argus.sources.base import SourceTask
from argus.sources.wayback import WaybackSourceAdapter
from argus.storage.sqlite import SQLiteRepository


class ProviderStub:
    async def captures(self, url):
        del url
        raise AssertionError("fetch is not used")

    async def health(self):
        return {"status": "ok"}


def _request() -> CollectionRequest:
    return CollectionRequest(
        consumer="wayback-dependency-test",
        analysis_id="wayback-dependency",
        territory={"city": "Пермь", "address": "Комсомольский проспект, 27"},
        intents=["historical_context"],
        constraints={"max_pages": 10},
    )


@pytest.mark.asyncio
async def test_wayback_capture_child_remains_high_priority_historical_dependency(
    tmp_path: Path,
):
    repository = SQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    adapter = WaybackSourceAdapter(ProviderStub(), SnapshotService(repository))
    task = SourceTask(
        source_id="wayback_cdx",
        goal="historical_context",
        url="https://example.org/perm-building",
        metadata={
            "collection_id": "collection",
            "curated_historical_round": 2,
            "historical_archive_dependency": True,
        },
    )
    capture = WaybackCapture(
        timestamp="20160102030405",
        original_url=task.url,
        capture_url=(
            "https://web.archive.org/web/20160102030405id_/"
            "https://example.org/perm-building"
        ),
    )

    result = await adapter.extract(
        task,
        WaybackCaptureResult(captures=[capture]),
        _request(),
    )

    assert len(result.discovered_tasks) == 1
    child = result.discovered_tasks[0]
    assert child.source_id == "generic_web"
    assert child.goal == "historical_context"
    assert child.metadata["historical_archive_dependency"] is True
    assert child.metadata["archive_dependency_version"] == "wayback-archive-dependency/1"
    assert child.metadata["curated_historical_round"] == 2
    assert child.metadata["research_goals"] == ["historical_context"]
    assert child.metadata["archive_original_url"] == task.url
    assert child.metadata["archive_timestamp"] == "20160102030405"
    assert child.metadata["disable_site_discovery"] is True
    await repository.close()
