import pytest

from argus.storage.fenced_postgres import FencedPostgresRepository
from argus.storage.lease_fencing import (
    WorkerStorageError,
    active_lease_fence,
    current_lease_fence,
    lease_fence,
)
from argus.storage.postgres import PostgresRepository


def test_lease_fence_is_scoped_and_resets():
    assert active_lease_fence() is None
    assert current_lease_fence("collection-1") is None

    with lease_fence("collection-1", "worker-1") as fence:
        assert active_lease_fence() == fence
        assert current_lease_fence("collection-1") == fence
        assert current_lease_fence("collection-2") is None

    assert active_lease_fence() is None


@pytest.mark.asyncio
async def test_worker_storage_failure_becomes_retry_signal(monkeypatch):
    async def failing_get_collection(self, collection_id):
        del self, collection_id
        raise RuntimeError("database connection dropped")

    monkeypatch.setattr(PostgresRepository, "get_collection", failing_get_collection)
    repository = FencedPostgresRepository(
        "postgresql://argus:secret@127.0.0.1:5432/argus",
        min_size=0,
        max_size=1,
    )

    with lease_fence("collection-1", "worker-1"):
        with pytest.raises(WorkerStorageError):
            await repository.get_collection("collection-1")


@pytest.mark.asyncio
async def test_non_worker_storage_failure_is_not_reclassified(monkeypatch):
    async def failing_get_collection(self, collection_id):
        del self, collection_id
        raise RuntimeError("database connection dropped")

    monkeypatch.setattr(PostgresRepository, "get_collection", failing_get_collection)
    repository = FencedPostgresRepository(
        "postgresql://argus:secret@127.0.0.1:5432/argus",
        min_size=0,
        max_size=1,
    )

    with pytest.raises(RuntimeError, match="database connection dropped"):
        await repository.get_collection("collection-1")
