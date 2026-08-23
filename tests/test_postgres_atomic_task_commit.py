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
    Snapshot,
    utcnow,
)
from argus.storage.fenced_postgres import FencedPostgresRepository
from argus.storage.lease_fencing import LeaseLostError, lease_fence
from argus.storage.postgres_migrations import run_postgres_migrations


def postgres_dsn() -> str:
    value = os.getenv("ARGUS_TEST_POSTGRES_DSN", "").strip()
    if not value:
        pytest.skip("ARGUS_TEST_POSTGRES_DSN is not configured")
    return value


def make_record(collection_id: str) -> CollectionRecord:
    timestamp = utcnow()
    return CollectionRecord(
        collection_id=collection_id,
        request=CollectionRequest(
            consumer="atomic-postgres-test",
            analysis_id=f"analysis-{uuid4()}",
            territory={"city": "Ижевск"},
            intents=["public_mentions"],
        ),
        status=CollectionStatus.RUNNING,
        stage="collecting:test",
        created_at=timestamp,
        updated_at=timestamp,
        checkpoint={"pending_tasks": [{"source_id": "test", "url": "https://example.com"}]},
    )


def factual_rows(record: CollectionRecord):
    observation = Observation(
        observation_id=f"obs-{uuid4()}",
        collection_id=record.collection_id,
        analysis_id=record.request.analysis_id,
        consumer=record.request.consumer,
        source="test",
        source_kind="document",
        url="https://example.com/atomic",
        entity_type="document",
        text="atomic fact",
        content_hash="a" * 64,
    )
    evidence = Evidence(
        evidence_id=f"evidence-{uuid4()}",
        observation_id=observation.observation_id,
        type="document",
        text="atomic fact",
        source=EvidenceSource(
            provider="test",
            url=observation.url,
            collected_at=observation.collected_at,
            source_id="test",
        ),
    )
    snapshot = Snapshot(
        snapshot_id=f"snapshot-{uuid4()}",
        source_id="test",
        source_url=observation.url,
        content_hash="b" * 64,
        extractor_version="argus/0.3.0",
        content_type="text/plain",
        content="atomic fact",
    )
    return observation, evidence, snapshot


@pytest.mark.asyncio
async def test_atomic_task_commit_is_fenced_by_current_lease_owner():
    dsn = postgres_dsn()
    await run_postgres_migrations(dsn)
    repository = FencedPostgresRepository(dsn, min_size=1, max_size=3, timeout_seconds=10)
    await repository.initialize()

    collection_id = f"atomic-fence-{uuid4()}"
    worker_a = f"worker-a-{uuid4()}"
    worker_b = f"worker-b-{uuid4()}"
    record = make_record(collection_id)
    observation, evidence, snapshot = factual_rows(record)

    try:
        await repository.register_worker(worker_a, metadata={"test": True})
        await repository.register_worker(worker_b, metadata={"test": True})
        await repository.create_collection(record)
        assert await repository.claim_next_collection(worker_a, lease_seconds=30) == collection_id

        async with repository._pool.connection() as conn:
            await conn.execute(
                """
                UPDATE argus.collection_leases
                SET lease_until=NOW() - INTERVAL '1 second'
                WHERE collection_id=%s
                """,
                (collection_id,),
            )
        assert await repository.claim_next_collection(worker_b, lease_seconds=30) == collection_id

        stale_commit = record.model_copy(deep=True)
        stale_commit.checkpoint = {"visited": ["test:https://example.com"]}
        stale_commit.updated_at = utcnow()
        with lease_fence(collection_id, worker_a):
            with pytest.raises(LeaseLostError):
                await repository.commit_task_success(
                    stale_commit,
                    observations=[observation],
                    evidence=[evidence],
                    snapshots=[snapshot],
                )

        stored = await repository.get_collection(collection_id)
        assert stored is not None
        assert stored.checkpoint == record.checkpoint
        assert await repository.list_observations(collection_id) == []
        assert await repository.list_evidence(collection_id) == []
        assert await repository.latest_snapshot(snapshot.source_url) is None

        owner_commit = record.model_copy(deep=True)
        owner_commit.checkpoint = {"visited": ["test:https://example.com"]}
        owner_commit.updated_at = utcnow()
        with lease_fence(collection_id, worker_b):
            await repository.commit_task_success(
                owner_commit,
                observations=[observation],
                evidence=[evidence],
                snapshots=[snapshot],
            )

        stored = await repository.get_collection(collection_id)
        assert stored is not None
        assert stored.checkpoint == owner_commit.checkpoint
        assert len(await repository.list_observations(collection_id)) == 1
        assert len(await repository.list_evidence(collection_id)) == 1
        latest = await repository.latest_snapshot(snapshot.source_url)
        assert latest is not None
        assert latest.snapshot_id == snapshot.snapshot_id
    finally:
        await repository.unregister_worker(worker_a)
        await repository.unregister_worker(worker_b)
        async with repository._pool.connection() as conn:
            await conn.execute(
                "DELETE FROM argus.collections WHERE collection_id=%s",
                (collection_id,),
            )
            await conn.execute(
                "DELETE FROM argus.snapshots WHERE snapshot_id=%s",
                (snapshot.snapshot_id,),
            )
        await repository.close()
