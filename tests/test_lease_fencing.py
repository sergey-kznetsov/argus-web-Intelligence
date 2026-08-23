from pathlib import Path

import pytest
from psycopg import OperationalError, ProgrammingError

from argus.config import Settings
from argus.storage.fenced_postgres import FencedPostgresRepository
from argus.storage.lease_fencing import (
    WorkerStorageError,
    active_lease_fence,
    current_lease_fence,
    lease_fence,
)
from argus.storage.postgres import PostgresRepository
from argus.worker import CollectionWorker


def test_lease_fence_is_scoped_and_resets():
    assert active_lease_fence() is None
    assert current_lease_fence("collection-1") is None

    with lease_fence("collection-1", "worker-1") as fence:
        assert active_lease_fence() == fence
        assert current_lease_fence("collection-1") == fence
        assert current_lease_fence("collection-2") is None

    assert active_lease_fence() is None


@pytest.mark.asyncio
async def test_worker_operational_storage_failure_becomes_retry_signal(monkeypatch):
    async def failing_get_collection(self, collection_id):
        del self, collection_id
        raise OperationalError("database connection dropped")

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
async def test_worker_programming_storage_failure_is_not_retried(monkeypatch):
    async def failing_get_collection(self, collection_id):
        del self, collection_id
        raise ProgrammingError("broken SQL")

    monkeypatch.setattr(PostgresRepository, "get_collection", failing_get_collection)
    repository = FencedPostgresRepository(
        "postgresql://argus:secret@127.0.0.1:5432/argus",
        min_size=0,
        max_size=1,
    )

    with lease_fence("collection-1", "worker-1"):
        with pytest.raises(ProgrammingError, match="broken SQL"):
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


@pytest.mark.asyncio
async def test_worker_installs_lease_fence_around_collection_execute(tmp_path: Path):
    settings = Settings(
        execution_role="worker",
        storage_backend="postgresql",
        database_dsn="postgresql://argus:secret@127.0.0.1:5432/argus",
        token_file=tmp_path / "token",
        browser_serp_enabled=False,
    )
    worker = CollectionWorker(settings)
    seen = []

    async def fake_execute(collection_id: str):
        seen.append(current_lease_fence(collection_id))

    worker.orchestrator.execute = fake_execute
    await worker._execute_owned_collection("collection-1")

    assert len(seen) == 1
    assert seen[0] is not None
    assert seen[0].collection_id == "collection-1"
    assert seen[0].worker_id == worker.worker_id
    assert active_lease_fence() is None
