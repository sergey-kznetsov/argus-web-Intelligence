from datetime import UTC, datetime
from pathlib import Path

import pytest

from argus.contracts.models import CollectionRecord, CollectionRequest, CollectionStatus
from argus.storage.sqlite import SQLiteRepository


@pytest.mark.asyncio
async def test_collection_roundtrip_and_recovery(tmp_path: Path):
    repo = SQLiteRepository(tmp_path / "db.sqlite")
    await repo.initialize()
    req = CollectionRequest(consumer="test", analysis_id="1", territory={"city": "Ижевск"}, intents=["reviews"])
    now = datetime.now(UTC)
    record = CollectionRecord(collection_id="c1", request=req, status=CollectionStatus.QUEUED,
                              created_at=now, updated_at=now)
    await repo.create_collection(record)
    loaded = await repo.get_collection("c1")
    assert loaded and loaded.request.consumer == "test"
    recoverable = await repo.list_recoverable_collections()
    assert [x.collection_id for x in recoverable] == ["c1"]
