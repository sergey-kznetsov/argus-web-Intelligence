import asyncio
import os
from uuid import uuid4

import pytest

from argus.contracts.models import CollectionRecord, CollectionRequest, CollectionStatus, utcnow
from argus.storage.base import IdempotencyConflictError
from argus.storage.postgres import PostgresRepository
from argus.storage.postgres_migrations import run_postgres_migrations


def postgres_dsn() -> str:
    value = os.getenv("ARGUS_TEST_POSTGRES_DSN", "").strip()
    if not value:
        pytest.skip("ARGUS_TEST_POSTGRES_DSN is not configured")
    return value


def record(collection_id: str, *, intent: str = "public_mentions") -> CollectionRecord:
    now = utcnow()
    return CollectionRecord(
        collection_id=collection_id,
        request=CollectionRequest(
            consumer="concurrent-test",
            analysis_id="analysis-concurrent-idempotency",
            idempotency_key="same-retry-key",
            territory={"city": "Ижевск"},
            intents=[intent],
        ),
        status=CollectionStatus.QUEUED,
        stage="queued",
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_concurrent_idempotent_create_returns_one_collection():
    dsn = postgres_dsn()
    await run_postgres_migrations(dsn)
    first = PostgresRepository(dsn, min_size=1, max_size=1, timeout_seconds=10)
    second = PostgresRepository(dsn, min_size=1, max_size=1, timeout_seconds=10)
    await asyncio.gather(first.initialize(), second.initialize())
    key = f"argus-v1:test-{uuid4()}"
    request_hash = "a" * 64
    first_record = record(f"idem-a-{uuid4()}")
    second_record = record(f"idem-b-{uuid4()}")
    try:
        result_a, result_b = await asyncio.gather(
            first.create_collection_idempotent(
                first_record,
                idempotency_key=key,
                request_hash=request_hash,
            ),
            second.create_collection_idempotent(
                second_record,
                idempotency_key=key,
                request_hash=request_hash,
            ),
        )
        stored_a, created_a = result_a
        stored_b, created_b = result_b
        assert stored_a.collection_id == stored_b.collection_id
        assert {created_a, created_b} == {True, False}
        assert stored_a.collection_id in {
            first_record.collection_id,
            second_record.collection_id,
        }
    finally:
        async with first._pool.connection() as conn:
            await conn.execute(
                "DELETE FROM argus.collection_idempotency WHERE idempotency_key=%s",
                (key,),
            )
            await conn.execute(
                "DELETE FROM argus.collections WHERE collection_id IN (%s, %s)",
                (first_record.collection_id, second_record.collection_id),
            )
        await asyncio.gather(first.close(), second.close())


@pytest.mark.asyncio
async def test_explicit_idempotency_key_rejects_different_request_hash():
    dsn = postgres_dsn()
    await run_postgres_migrations(dsn)
    repository = PostgresRepository(dsn, min_size=1, max_size=1, timeout_seconds=10)
    await repository.initialize()
    key = f"argus-v1:conflict-{uuid4()}"
    first_record = record(f"idem-first-{uuid4()}")
    conflicting_record = record(f"idem-conflict-{uuid4()}", intent="local_news")
    try:
        stored, created = await repository.create_collection_idempotent(
            first_record,
            idempotency_key=key,
            request_hash="b" * 64,
        )
        assert created is True
        assert stored.collection_id == first_record.collection_id

        with pytest.raises(IdempotencyConflictError):
            await repository.create_collection_idempotent(
                conflicting_record,
                idempotency_key=key,
                request_hash="c" * 64,
            )
        assert await repository.get_collection(conflicting_record.collection_id) is None
    finally:
        async with repository._pool.connection() as conn:
            await conn.execute(
                "DELETE FROM argus.collection_idempotency WHERE idempotency_key=%s",
                (key,),
            )
            await conn.execute(
                "DELETE FROM argus.collections WHERE collection_id IN (%s, %s)",
                (first_record.collection_id, conflicting_record.collection_id),
            )
        await repository.close()
