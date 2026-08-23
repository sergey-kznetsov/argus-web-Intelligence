from __future__ import annotations

import os
from uuid import uuid4

import pytest

from argus.contracts.models import CollectionRecord, CollectionRequest, CollectionStatus, utcnow
from argus.storage.postgres import PostgresRepository
from argus.storage.postgres_migrations import run_postgres_migrations
from argus.storage.postgres_storage_stats import postgres_storage_stats


def postgres_dsn() -> str:
    value = os.getenv("ARGUS_TEST_POSTGRES_DSN", "").strip()
    if not value:
        pytest.skip("ARGUS_TEST_POSTGRES_DSN is not configured")
    return value


@pytest.mark.asyncio
async def test_storage_stats_report_jsonb_and_relation_growth():
    dsn = postgres_dsn()
    await run_postgres_migrations(dsn)
    repository = PostgresRepository(dsn, min_size=1, max_size=2, timeout_seconds=10)
    await repository.initialize()
    collection_id = f"storage-stats-{uuid4()}"
    timestamp = utcnow()
    record = CollectionRecord(
        collection_id=collection_id,
        request=CollectionRequest(
            consumer="storage-stats-test",
            analysis_id=f"analysis-{uuid4()}",
            territory={"city": "Ижевск", "address": "Пушкинская, 277"},
            intents=["public_mentions"],
        ),
        status=CollectionStatus.COMPLETED,
        stage="completed",
        created_at=timestamp,
        updated_at=timestamp,
        checkpoint={"probe": "x" * 4096},
    )
    try:
        await repository.create_collection(record)
        stats = await postgres_storage_stats(dsn)

        assert stats["schema"] == "argus"
        assert stats["total_relation_bytes"] > 0
        assert stats["largest_jsonb_table"] in stats["jsonb"]
        assert stats["largest_jsonb_row_table"] in stats["jsonb"]
        assert stats["jsonb"]["collections"]["rows"] >= 1
        assert stats["jsonb"]["collections"]["max_body_bytes"] > 0
        assert stats["relations"]["collections"]["table_bytes"] > 0
        assert stats["relations"]["collections"]["total_bytes"] >= (
            stats["relations"]["collections"]["table_bytes"]
        )
        assert "collection_result_access" in stats["relations"]
    finally:
        async with repository._pool.connection() as conn:
            await conn.execute(
                "DELETE FROM argus.collections WHERE collection_id=%s",
                (collection_id,),
            )
        await repository.close()
