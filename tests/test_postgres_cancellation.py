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
from argus.storage.postgres import PostgresRepository
from argus.storage.postgres_migrations import run_postgres_migrations


def postgres_dsn() -> str:
    value = os.getenv("ARGUS_TEST_POSTGRES_DSN", "").strip()
    if not value:
        pytest.skip("ARGUS_TEST_POSTGRES_DSN is not configured")
    return value


@pytest.mark.asyncio
async def test_cancelled_collection_rejects_stale_worker_state_and_new_results():
    dsn = postgres_dsn()
    await run_postgres_migrations(dsn)
    repository = PostgresRepository(dsn, min_size=1, max_size=2, timeout_seconds=10)
    await repository.initialize()
    collection_id = f"cancel-race-{uuid4()}"
    analysis_id = f"analysis-{uuid4()}"
    try:
        created_at = utcnow()
        running = CollectionRecord(
            collection_id=collection_id,
            request=CollectionRequest(
                consumer="cancel-race-test",
                analysis_id=analysis_id,
                territory={"city": "Ижевск"},
                intents=["public_mentions"],
            ),
            status=CollectionStatus.RUNNING,
            stage="collecting",
            created_at=created_at,
            updated_at=created_at,
        )
        await repository.create_collection(running)
        stale_worker_record = running.model_copy(deep=True)

        cancelled = running.model_copy(deep=True)
        cancelled.status = CollectionStatus.CANCELLED
        cancelled.stage = "cancelled"
        cancelled.updated_at = utcnow()
        await repository.update_collection(cancelled)

        stale_worker_record.status = CollectionStatus.RUNNING
        stale_worker_record.stage = "collecting:stale-worker"
        stale_worker_record.updated_at = utcnow()
        await repository.update_collection(stale_worker_record)

        loaded = await repository.get_collection(collection_id)
        assert loaded is not None
        assert loaded.status == CollectionStatus.CANCELLED
        assert loaded.stage == "cancelled"

        observation = Observation(
            observation_id=f"obs-{uuid4()}",
            collection_id=collection_id,
            analysis_id=analysis_id,
            consumer="cancel-race-test",
            source="test",
            source_kind="document",
            url="https://example.com/cancelled",
            entity_type="document",
            title="Cancelled result",
            text="This result arrived after cancellation",
            content_hash="c" * 64,
        )
        evidence = Evidence(
            evidence_id=f"evidence-{uuid4()}",
            observation_id=observation.observation_id,
            type="document",
            text=observation.text,
            source=EvidenceSource(
                provider="test",
                url=observation.url,
                collected_at=utcnow(),
                source_id="test",
            ),
        )
        await repository.add_observation(observation)
        await repository.add_evidence(evidence, collection_id)

        assert await repository.list_observations(collection_id) == []
        assert await repository.list_evidence(collection_id) == []
    finally:
        await repository.close()
