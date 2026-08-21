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
