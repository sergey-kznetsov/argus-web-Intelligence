import os
from datetime import timedelta
from uuid import uuid4

import pytest

from argus.contracts.models import CollectionRecord, CollectionRequest, CollectionStatus, utcnow
from argus.storage.postgres import PostgresRepository
from argus.storage.postgres_migrations import run_postgres_migrations


def postgres_dsn() -> str:
    value = os.getenv("ARGUS_TEST_POSTGRES_DSN", "").strip()
    if not value:
        pytest.skip("ARGUS_TEST_POSTGRES_DSN is not configured")
    return value


def record(collection_id: str, status: CollectionStatus, created_at) -> CollectionRecord:
    return CollectionRecord(
        collection_id=collection_id,
        request=CollectionRequest(
            consumer="fairness-test",
            analysis_id=f"analysis-{collection_id}",
            territory={"city": "Ижевск"},
            intents=["public_mentions"],
        ),
        status=status,
        stage=status.value,
        created_at=created_at,
        updated_at=created_at,
    )


@pytest.mark.asyncio
async def test_older_recovered_running_collection_is_not_starved_by_new_queue():
    dsn = postgres_dsn()
    await run_postgres_migrations(dsn)
    repository = PostgresRepository(dsn, min_size=1, max_size=2, timeout_seconds=10)
    await repository.initialize()
    suffix = uuid4().hex
    older_id = f"fairness-running-{suffix}"
    newer_id = f"fairness-queued-{suffix}"
    worker_id = f"fairness-worker-{suffix}"
    now = utcnow()
    try:
        await repository.create_collection(
            record(older_id, CollectionStatus.RUNNING, now - timedelta(minutes=5))
        )
        await repository.create_collection(
            record(newer_id, CollectionStatus.QUEUED, now)
        )

        claimed = await repository.claim_next_collection(worker_id, lease_seconds=30)
        assert claimed == older_id
        await repository.release_collection_lease(older_id, worker_id)

        older = await repository.get_collection(older_id)
        newer = await repository.get_collection(newer_id)
        assert older is not None
        assert newer is not None
        older.status = CollectionStatus.CANCELLED
        newer.status = CollectionStatus.CANCELLED
        older.updated_at = utcnow()
        newer.updated_at = utcnow()
        await repository.update_collection(older)
        await repository.update_collection(newer)
    finally:
        async with repository._pool.connection() as conn:
            await conn.execute(
                "DELETE FROM argus.collections WHERE collection_id IN (%s, %s)",
                (older_id, newer_id),
            )
        await repository.close()
