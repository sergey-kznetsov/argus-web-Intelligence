import os
from uuid import uuid4

import pytest

from argus.contracts.models import (
    CollectionRecord,
    CollectionRequest,
    CollectionStatus,
    Evidence,
    EvidenceSource,
    Observation,
    utcnow,
)
from argus.result_delivery import ResultTooLargeError
from argus.storage.postgres import PostgresRepository
from argus.storage.postgres_migrations import run_postgres_migrations
from argus.storage.postgres_operations import PostgresOperationsStore


def postgres_dsn() -> str:
    value = os.getenv("ARGUS_TEST_POSTGRES_DSN", "").strip()
    if not value:
        pytest.skip("ARGUS_TEST_POSTGRES_DSN is not configured")
    return value


@pytest.mark.asyncio
async def test_postgres_bounded_result_and_keyset_pages():
    dsn = postgres_dsn()
    await run_postgres_migrations(dsn)
    repository = PostgresRepository(dsn, min_size=1, max_size=2, timeout_seconds=10)
    read_store = PostgresOperationsStore(dsn, min_size=0, max_size=2, timeout_seconds=10)
    await repository.initialize()
    await read_store.initialize()

    collection_id = f"result-{uuid4()}"
    timestamp = utcnow()
    record = CollectionRecord(
        collection_id=collection_id,
        request=CollectionRequest(
            consumer="postgres-result-test",
            analysis_id=f"analysis-{uuid4()}",
            territory={"city": "Ижевск"},
            intents=["public_mentions"],
        ),
        status=CollectionStatus.COMPLETED,
        stage="completed",
        created_at=timestamp,
        updated_at=timestamp,
    )

    try:
        await repository.create_collection(record)
        for index in range(3):
            observation = Observation(
                observation_id=f"{collection_id}-obs-{index:03d}",
                collection_id=collection_id,
                analysis_id=record.request.analysis_id,
                consumer=record.request.consumer,
                source="test",
                source_kind="document",
                url=f"https://example.com/{collection_id}/{index}",
                entity_type="document",
                text=f"observation-{index}",
                content_hash=(f"{index + 1:x}" * 64)[:64],
            )
            await repository.add_observation(observation)
            await repository.add_evidence(
                Evidence(
                    evidence_id=f"{collection_id}-evidence-{index:03d}",
                    observation_id=observation.observation_id,
                    type="document",
                    text=f"evidence-{index}",
                    source=EvidenceSource(
                        provider="test",
                        url=observation.url,
                        collected_at=utcnow(),
                        source_id="test",
                    ),
                ),
                collection_id,
            )

        record_and_stats = await read_store.result_stats(collection_id)
        assert record_and_stats is not None
        stored_record, stats = record_and_stats
        assert stored_record.status == CollectionStatus.COMPLETED
        assert stats.observation_count == 3
        assert stats.evidence_count == 3
        assert stats.stored_bytes > 0

        with pytest.raises(ResultTooLargeError):
            await read_store.read_bounded_result(
                collection_id,
                max_items=4,
                max_bytes=10_000_000,
            )

        bundle = await read_store.read_bounded_result(
            collection_id,
            max_items=10,
            max_bytes=10_000_000,
        )
        assert bundle is not None
        assert len(bundle.observations) == 3
        assert len(bundle.evidence) == 3

        first = await read_store.observation_page(
            collection_id,
            after_id=None,
            limit=1,
        )
        assert first is not None
        assert first.total_count == 3
        assert len(first.items) == 1
        assert first.has_more is True

        second = await read_store.observation_page(
            collection_id,
            after_id=first.items[-1].observation_id,
            limit=1,
        )
        assert second is not None
        assert len(second.items) == 1
        assert second.items[0].observation_id > first.items[0].observation_id

        evidence = await read_store.evidence_page(
            collection_id,
            after_id=None,
            limit=2,
        )
        assert evidence is not None
        assert evidence.total_count == 3
        assert len(evidence.items) == 2
        assert evidence.has_more is True
    finally:
        async with repository._pool.connection() as conn:
            await conn.execute(
                "DELETE FROM argus.collections WHERE collection_id=%s",
                (collection_id,),
            )
        await read_store.close()
        await repository.close()
