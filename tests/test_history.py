from pathlib import Path

import pytest

from argus.history.snapshots import SnapshotService
from argus.storage.sqlite import SQLiteRepository


@pytest.mark.asyncio
async def test_snapshot_hash_and_diff(tmp_path: Path):
    repo = SQLiteRepository(tmp_path / "db.sqlite")
    await repo.initialize()
    service = SnapshotService(repo)
    first = await service.capture("web", "https://example.com", "a\nb")
    second = await service.capture("web", "https://example.com", "a\nc")
    assert first.content_hash != second.content_hash
    assert second.previous_snapshot_id == first.snapshot_id
    assert "-b" in (second.diff or "")
    assert "+c" in (second.diff or "")


@pytest.mark.asyncio
async def test_unchanged_snapshot_is_still_persisted_for_provenance(tmp_path: Path):
    repo = SQLiteRepository(tmp_path / "db.sqlite")
    await repo.initialize()
    service = SnapshotService(repo)
    first = await service.capture("web", "https://example.com", "same")
    second = await service.capture("web", "https://example.com", "same")
    latest = await repo.latest_snapshot("https://example.com")

    assert second.snapshot_id != first.snapshot_id
    assert second.previous_snapshot_id == first.snapshot_id
    assert second.diff is None
    assert latest is not None
    assert latest.snapshot_id == second.snapshot_id


@pytest.mark.asyncio
async def test_collection_snapshot_replay_reuses_stable_identity(tmp_path: Path):
    repo = SQLiteRepository(tmp_path / "db.sqlite")
    await repo.initialize()
    service = SnapshotService(repo)

    first = await service.capture(
        "web",
        "https://example.com/page",
        "same",
        collection_id="collection-1",
    )
    replay = await service.capture(
        "web",
        "https://example.com/page",
        "same",
        collection_id="collection-1",
    )

    assert replay.snapshot_id == first.snapshot_id
    assert replay.collected_at == first.collected_at


@pytest.mark.asyncio
async def test_new_collection_gets_new_snapshot_identity(tmp_path: Path):
    repo = SQLiteRepository(tmp_path / "db.sqlite")
    await repo.initialize()
    service = SnapshotService(repo)

    first = await service.capture(
        "web",
        "https://example.com/page",
        "same",
        collection_id="collection-1",
    )
    second = await service.capture(
        "web",
        "https://example.com/page",
        "same",
        collection_id="collection-2",
    )

    assert second.snapshot_id != first.snapshot_id
    assert second.previous_snapshot_id == first.snapshot_id


@pytest.mark.asyncio
async def test_old_collection_replay_after_newer_snapshot_does_not_duplicate(tmp_path: Path):
    repo = SQLiteRepository(tmp_path / "db.sqlite")
    await repo.initialize()
    service = SnapshotService(repo)

    first = await service.capture(
        "web",
        "https://example.com/page",
        "same",
        collection_id="collection-1",
    )
    second = await service.capture(
        "web",
        "https://example.com/page",
        "same",
        collection_id="collection-2",
    )
    replay = await service.capture(
        "web",
        "https://example.com/page",
        "same",
        collection_id="collection-1",
    )
    latest = await repo.latest_snapshot("https://example.com/page")

    assert replay.snapshot_id == first.snapshot_id
    assert latest is not None
    assert latest.snapshot_id == second.snapshot_id
