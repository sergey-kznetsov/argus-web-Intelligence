import os
from datetime import timedelta
from uuid import uuid4

import pytest

from argus.contracts.models import (
    CollectionRecord,
    CollectionRequest,
    CollectionStatus,
    StructuredError,
    utcnow,
)
from argus.pagination import CollectionCursor
from argus.storage.postgres import PostgresRepository
from argus.storage.postgres_migrations import run_postgres_migrations
from argus.storage.postgres_operations import PostgresOperationsStore


def postgres_dsn() -> str:
    value = os.getenv("ARGUS_TEST_POSTGRES_DSN", "").strip()
    if not value:
        pytest.skip("ARGUS_TEST_POSTGRES_DSN is not configured")
    return value


def record(
    collection_id: str,
    *,
    consumer: str,
    status: CollectionStatus,
    age_seconds: int,
    with_error: bool = False,
) -> CollectionRecord:
    timestamp = utcnow() - timedelta(seconds=age_seconds)
    errors = (
        [StructuredError(code="TEST_ERROR", message="test", retryable=False)]
        if with_error
        else []
    )
    return CollectionRecord(
        collection_id=collection_id,
        request=CollectionRequest(
            consumer=consumer,
            analysis_id=f"analysis-{collection_id}",
            territory={"city": "Ижевск"},
            intents=["public_mentions"],
        ),
        status=status,
        stage=status.value,
        created_at=timestamp,
        updated_at=timestamp,
        progress_percent=100 if status == CollectionStatus.COMPLETED else 25,
        partial=False,
        errors=errors,
    )


@pytest.mark.asyncio
async def test_collection_operations_use_stable_keyset_pagination_and_filters():
    dsn = postgres_dsn()
    await run_postgres_migrations(dsn)
    repository = PostgresRepository(dsn, min_size=1, max_size=2, timeout_seconds=10)
    await repository.initialize()
    store = PostgresOperationsStore(dsn)
    await store.initialize()
    consumer = f"operations-{uuid4()}"
    records = [
        record(
            f"operations-new-{uuid4()}",
            consumer=consumer,
            status=CollectionStatus.QUEUED,
            age_seconds=1,
            with_error=True,
        ),
        record(
            f"operations-mid-{uuid4()}",
            consumer=consumer,
            status=CollectionStatus.COMPLETED,
            age_seconds=2,
        ),
        record(
            f"operations-old-{uuid4()}",
            consumer=consumer,
            status=CollectionStatus.COMPLETED,
            age_seconds=3,
        ),
    ]
    try:
        for item in records:
            await repository.create_collection(item)

        first_page, has_more = await store.list_collections(
            limit=2,
            consumer=consumer,
        )
        assert has_more is True
        assert [item.collection_id for item in first_page] == [
            records[0].collection_id,
            records[1].collection_id,
        ]
        assert first_page[0].error_count == 1
        assert first_page[0].observation_count == 0
        assert first_page[0].evidence_count == 0

        second_page, has_more = await store.list_collections(
            limit=2,
            consumer=consumer,
            cursor=CollectionCursor(
                created_at=first_page[-1].created_at,
                collection_id=first_page[-1].collection_id,
            ),
        )
        assert has_more is False
        assert [item.collection_id for item in second_page] == [records[2].collection_id]

        completed, has_more = await store.list_collections(
            limit=10,
            consumer=consumer,
            status=CollectionStatus.COMPLETED,
        )
        assert has_more is False
        assert [item.collection_id for item in completed] == [
            records[1].collection_id,
            records[2].collection_id,
        ]
    finally:
        async with repository._pool.connection() as conn:
            await conn.execute(
                "DELETE FROM argus.collections WHERE collection_id = ANY(%s)",
                ([item.collection_id for item in records],),
            )
        await store.close()
        await repository.close()
