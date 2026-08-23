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
            consumer="lease-fencing-test",
            analysis_id=f"analysis-{uuid4()}",
            territory={"city": "Ижевск"},
            intents=["public_mentions"],
        ),
        status=CollectionStatus.QUEUED,
        created_at=timestamp,
        updated_at=timestamp,
    )


@pytest.mark.asyncio
async def test_stale_worker_cannot_mutate_after_lease_transfer():
    dsn = postgres_dsn()
    await run_postgres_migrations(dsn)
    repository = FencedPostgresRepository(dsn, min_size=1, max_size=3, timeout_seconds=10)
    await repository.initialize()

    collection_id = f"fence-{uuid4()}"
    worker_a = f"worker-a-{uuid4()}"
    worker_b = f"worker-b-{uuid4()}"
    record = make_record(collection_id)

    try:
        await repository.register_worker(worker_a, metadata={"test": True})
        await repository.register_worker(worker_b, metadata={"test": True})
        await repository.create_collection(record)
        assert await repository.claim_next_collection(worker_a, lease_seconds=30) == collection_id

        with lease_fence(collection_id, worker_a):
            record.status = CollectionStatus.RUNNING
            record.stage = "owned-by-a"
            record.updated_at = utcnow()
            await repository.update_collection(record)

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

        stale_observation = Observation(
            observation_id=f"obs-{uuid4()}",
            collection_id=collection_id,
            analysis_id=record.request.analysis_id,
            consumer=record.request.consumer,
            source="test",
            source_kind="document",
            url="https://example.com/stale",
            entity_type="document",
            text="stale",
            content_hash="a" * 64,
        )
        stale_evidence = Evidence(
            evidence_id=f"evidence-{uuid4()}",
            observation_id=stale_observation.observation_id,
            type="document",
            text="stale",
            source=EvidenceSource(
                provider="test",
                url=stale_observation.url,
                collected_at=utcnow(),
                source_id="test",
            ),
        )
        stale_snapshot = Snapshot(
            snapshot_id=f"snapshot-{uuid4()}",
            source_id="test",
            source_url="https://example.com/stale",
            content_hash="b" * 64,
            extractor_version="argus/0.3.0",
            content_type="text/plain",
            content="stale",
        )

        with lease_fence(collection_id, worker_a):
            record.stage = "stale-write"
            record.updated_at = utcnow()
            with pytest.raises(LeaseLostError):
                await repository.update_collection(record)
            with pytest.raises(LeaseLostError):
                await repository.add_observation(stale_observation)
            with pytest.raises(LeaseLostError):
                await repository.add_evidence(stale_evidence, collection_id)
            with pytest.raises(LeaseLostError):
                await repository.add_snapshot(stale_snapshot, collection_id=collection_id)

        assert await repository.list_observations(collection_id) == []
        assert await repository.list_evidence(collection_id) == []
        assert await repository.latest_snapshot(stale_snapshot.source_url) is None

        with lease_fence(collection_id, worker_b):
            record.stage = "owned-by-b"
            record.updated_at = utcnow()
            await repository.update_collection(record)
            await repository.add_observation(stale_observation)
            await repository.add_evidence(stale_evidence, collection_id)
            await repository.add_snapshot(stale_snapshot, collection_id=collection_id)

        stored = await repository.get_collection(collection_id)
        assert stored is not None
        assert stored.stage == "owned-by-b"
        assert len(await repository.list_observations(collection_id)) == 1
        assert len(await repository.list_evidence(collection_id)) == 1
        snapshot = await repository.latest_snapshot(stale_snapshot.source_url)
        assert snapshot is not None
        assert snapshot.snapshot_id == stale_snapshot.snapshot_id
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
                (stale_snapshot.snapshot_id if "stale_snapshot" in locals() else "",),
            )
        await repository.close()
