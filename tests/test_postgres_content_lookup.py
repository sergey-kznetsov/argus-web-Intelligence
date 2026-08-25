from __future__ import annotations

import os
from uuid import uuid4

import pytest

from argus.contracts.models import (
    CollectionRecord,
    CollectionRequest,
    CollectionStatus,
    Observation,
    utcnow,
)
from argus.storage.content_postgres import ContentAwareFencedPostgresRepository
from argus.storage.postgres_migrations import EXPECTED_SCHEMA_VERSION, run_postgres_migrations


def postgres_dsn() -> str:
    value = os.getenv("ARGUS_TEST_POSTGRES_DSN", "").strip()
    if not value:
        pytest.skip("ARGUS_TEST_POSTGRES_DSN is not configured")
    return value


@pytest.mark.asyncio
async def test_postgres_committed_content_lookup_uses_schema_v8_contract():
    dsn = postgres_dsn()
    applied = await run_postgres_migrations(dsn)
    assert EXPECTED_SCHEMA_VERSION >= 8
    assert all(version <= EXPECTED_SCHEMA_VERSION for version in applied)

    repository = ContentAwareFencedPostgresRepository(
        dsn,
        min_size=1,
        max_size=2,
        timeout_seconds=10,
    )
    await repository.initialize()
    collection_id = f"content-lookup-{uuid4()}"
    timestamp = utcnow()
    request = CollectionRequest(
        consumer="postgres-content-lookup-test",
        analysis_id=f"analysis-{uuid4()}",
        territory={"city": "Ижевск"},
        intents=["public_mentions"],
    )
    record = CollectionRecord(
        collection_id=collection_id,
        request=request,
        status=CollectionStatus.RUNNING,
        stage="collecting",
        created_at=timestamp,
        updated_at=timestamp,
    )

    try:
        await repository.create_collection(record)
        content_hash = "a" * 64
        page = Observation(
            observation_id=f"{collection_id}-page",
            collection_id=collection_id,
            analysis_id=request.analysis_id,
            consumer=request.consumer,
            source="generic_web",
            source_kind="web_page",
            url="https://example.com/page",
            entity_type="document",
            text="x" * 500,
            content_hash=content_hash,
        )
        embedded = Observation(
            observation_id=f"{collection_id}-jsonld",
            collection_id=collection_id,
            analysis_id=request.analysis_id,
            consumer=request.consumer,
            source="generic_web",
            source_kind="json_ld",
            url="https://example.com/page",
            entity_type="publication",
            title="Entity",
            content_hash=content_hash,
        )
        await repository.add_observation(page)
        await repository.add_observation(embedded)

        found = await repository.find_observation_by_content_hash(
            collection_id,
            content_hash=content_hash,
            source_kinds=["web_page"],
        )
        wrong_kind = await repository.find_observation_by_content_hash(
            collection_id,
            content_hash=content_hash,
            source_kinds=["structured_data"],
        )

        assert found is not None
        assert found.observation_id == page.observation_id
        assert wrong_kind is None

        async with repository._pool.connection() as conn:
            row = await (
                await conn.execute(
                    "SELECT to_regclass('argus.ix_argus_observations_content_identity') AS index_name"
                )
            ).fetchone()
        assert row is not None
        assert row["index_name"] is not None
    finally:
        try:
            async with repository._pool.connection() as conn:
                await conn.execute(
                    "DELETE FROM argus.collections WHERE collection_id=%s",
                    (collection_id,),
                )
        finally:
            await repository.close()
