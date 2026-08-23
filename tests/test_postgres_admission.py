import asyncio
import os
from uuid import uuid4

import psycopg
import pytest

from argus.contracts.models import CollectionRecord, CollectionRequest, CollectionStatus, utcnow
from argus.storage.base import QueueCapacityError
from argus.storage.postgres import PostgresRepository
from argus.storage.postgres_migrations import run_postgres_migrations


def postgres_dsn() -> str:
    value = os.getenv("ARGUS_TEST_POSTGRES_DSN", "").strip()
    if not value:
        pytest.skip("ARGUS_TEST_POSTGRES_DSN is not configured")
    return value


def record(collection_id: str, consumer: str) -> CollectionRecord:
    now = utcnow()
    return CollectionRecord(
        collection_id=collection_id,
        request=CollectionRequest(
            consumer=consumer,
            analysis_id=f"analysis-{collection_id}",
            territory={"city": "Ижевск"},
            intents=["public_mentions"],
        ),
        status=CollectionStatus.QUEUED,
        stage="queued",
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_concurrent_admission_cannot_oversubscribe_last_global_slot():
    dsn = postgres_dsn()
    await run_postgres_migrations(dsn)
    with psycopg.connect(dsn) as conn:
        baseline = int(
            conn.execute(
                "SELECT COUNT(*) FROM argus.collections "
                "WHERE status IN ('queued', 'running')"
            ).fetchone()[0]
        )

    repository_a = PostgresRepository(dsn, min_size=1, max_size=1, timeout_seconds=10)
    repository_b = PostgresRepository(dsn, min_size=1, max_size=1, timeout_seconds=10)
    await asyncio.gather(repository_a.initialize(), repository_b.initialize())
    suffix = uuid4().hex
    record_a = record(f"admission-a-{suffix}", f"consumer-a-{suffix}")
    record_b = record(f"admission-b-{suffix}", f"consumer-b-{suffix}")
    key_a = f"argus-v1:admission-a-{suffix}"
    key_b = f"argus-v1:admission-b-{suffix}"
    created_ids: set[str] = set()
    try:
        results = await asyncio.gather(
            repository_a.create_collection_idempotent(
                record_a,
                idempotency_key=key_a,
                request_hash="a" * 64,
                max_active_collections=baseline + 1,
            ),
            repository_b.create_collection_idempotent(
                record_b,
                idempotency_key=key_b,
                request_hash="b" * 64,
                max_active_collections=baseline + 1,
            ),
            return_exceptions=True,
        )

        successes = [item for item in results if isinstance(item, tuple)]
        failures = [item for item in results if isinstance(item, QueueCapacityError)]
        assert len(successes) == 1
        assert len(failures) == 1
        assert failures[0].scope == "global"
        stored, created = successes[0]
        assert created is True
        created_ids.add(stored.collection_id)

        missing_id = (
            record_b.collection_id
            if stored.collection_id == record_a.collection_id
            else record_a.collection_id
        )
        assert await repository_a.get_collection(missing_id) is None
    finally:
        async with repository_a._pool.connection() as conn:
            if created_ids:
                await conn.execute(
                    "DELETE FROM argus.collections WHERE collection_id = ANY(%s)",
                    (list(created_ids),),
                )
        await asyncio.gather(repository_a.close(), repository_b.close())
