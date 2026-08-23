import os
from datetime import timedelta
from uuid import uuid4

import pytest

from argus.contracts.models import (
    CollectionRecord,
    CollectionRequest,
    CollectionStatus,
    Snapshot,
    utcnow,
)
from argus.storage.postgres import PostgresRepository
from argus.storage.postgres_migrations import run_postgres_migrations


def postgres_dsn() -> str:
    value = os.getenv("ARGUS_TEST_POSTGRES_DSN", "").strip()
    if not value:
        pytest.skip("ARGUS_TEST_POSTGRES_DSN is not configured")
    return value


def make_collection(
    collection_id: str,
    *,
    status: CollectionStatus,
    age_days: int = 0,
) -> CollectionRecord:
    timestamp = utcnow() - timedelta(days=age_days)
    return CollectionRecord(
        collection_id=collection_id,
        request=CollectionRequest(
            consumer="operations-test",
            analysis_id=f"analysis-{collection_id}",
            territory={"city": "Ижевск"},
            intents=["public_mentions"],
        ),
        status=status,
        stage=status.value,
        created_at=timestamp,
        updated_at=timestamp,
    )


@pytest.mark.asyncio
async def test_queue_metrics_report_delta_for_collection_worker_and_lease():
    dsn = postgres_dsn()
    await run_postgres_migrations(dsn)
    repository = PostgresRepository(dsn, min_size=1, max_size=2, timeout_seconds=10)
    await repository.initialize()
    collection_id = f"metrics-{uuid4()}"
    worker_id = f"metrics-worker-{uuid4()}"
    try:
        before = await repository.queue_metrics(worker_max_age_seconds=60)
        await repository.create_collection(
            make_collection(collection_id, status=CollectionStatus.QUEUED)
        )
        await repository.register_worker(worker_id, metadata={"test": True})

        queued = await repository.queue_metrics(worker_max_age_seconds=60)
        assert queued.queued == before.queued + 1
        assert queued.active_workers == before.active_workers + 1
        assert queued.oldest_queued_age_seconds is not None

        async with repository._pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO argus.collection_leases(
                  collection_id, worker_id, leased_at, heartbeat_at, lease_until
                ) VALUES(%s, %s, NOW(), NOW(), NOW() + INTERVAL '30 seconds')
                """,
                (collection_id, worker_id),
            )
        leased = await repository.queue_metrics(worker_max_age_seconds=60)
        assert leased.active_leases == before.active_leases + 1
    finally:
        await repository.release_collection_lease(collection_id, worker_id)
        await repository.unregister_worker(worker_id)
        async with repository._pool.connection() as conn:
            await conn.execute(
                "DELETE FROM argus.collections WHERE collection_id=%s",
                (collection_id,),
            )
        await repository.close()


@pytest.mark.asyncio
async def test_retention_keeps_active_collection_and_latest_snapshot_per_url():
    dsn = postgres_dsn()
    await run_postgres_migrations(dsn)
    repository = PostgresRepository(dsn, min_size=1, max_size=2, timeout_seconds=10)
    await repository.initialize()

    active_id = f"retention-active-{uuid4()}"
    terminal_id = f"retention-terminal-{uuid4()}"
    idempotency_key = f"retention-key-{uuid4()}"
    stale_worker_id = f"retention-worker-{uuid4()}"
    source_url = f"https://example.com/{uuid4()}"
    snapshot_ids = [f"retention-snapshot-{uuid4()}" for _ in range(3)]
    try:
        await repository.create_collection(
            make_collection(
                active_id,
                status=CollectionStatus.QUEUED,
                age_days=400,
            )
        )
        terminal = make_collection(
            terminal_id,
            status=CollectionStatus.COMPLETED,
            age_days=400,
        )
        await repository.create_collection(terminal)
        async with repository._pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO argus.collection_idempotency(
                  idempotency_key, collection_id, request_hash, created_at
                ) VALUES(%s, %s, %s, NOW() - INTERVAL '2 days')
                """,
                (idempotency_key, terminal_id, "f" * 64),
            )
            await conn.execute(
                """
                INSERT INTO argus.worker_instances(
                  worker_id, started_at, heartbeat_at, metadata
                ) VALUES(
                  %s,
                  NOW() - INTERVAL '10 days',
                  NOW() - INTERVAL '10 days',
                  '{}'::jsonb
                )
                """,
                (stale_worker_id,),
            )

        for snapshot_id, age_days in zip(snapshot_ids, (500, 450, 400), strict=True):
            collected_at = utcnow() - timedelta(days=age_days)
            await repository.add_snapshot(
                Snapshot(
                    snapshot_id=snapshot_id,
                    source_id="retention-test",
                    source_url=source_url,
                    collected_at=collected_at,
                    content_hash=(snapshot_id[-1] * 64)[:64],
                    extractor_version="test",
                    content_type="text/html",
                    content=f"snapshot-{age_days}",
                )
            )

        result = await repository.run_retention(
            idempotency_window_seconds=86_400,
            collection_retention_days=180,
            snapshot_retention_days=365,
            worker_registration_retention_days=7,
            batch_size=1000,
        )

        assert result.idempotency_deleted >= 1
        assert result.collections_deleted >= 1
        assert result.snapshots_deleted >= 2
        assert result.workers_deleted >= 1
        assert await repository.get_collection(active_id) is not None
        assert await repository.get_collection(terminal_id) is None

        async with repository._pool.connection() as conn:
            rows = await (
                await conn.execute(
                    """
                    SELECT snapshot_id FROM argus.snapshots
                    WHERE source_url=%s
                    ORDER BY collected_at DESC
                    """,
                    (source_url,),
                )
            ).fetchall()
            stale_worker = await (
                await conn.execute(
                    "SELECT worker_id FROM argus.worker_instances WHERE worker_id=%s",
                    (stale_worker_id,),
                )
            ).fetchone()
        assert [str(row["snapshot_id"]) for row in rows] == [snapshot_ids[2]]
        assert stale_worker is None
    finally:
        async with repository._pool.connection() as conn:
            await conn.execute(
                "DELETE FROM argus.collection_idempotency WHERE idempotency_key=%s",
                (idempotency_key,),
            )
            await conn.execute(
                "DELETE FROM argus.collections WHERE collection_id IN (%s, %s)",
                (active_id, terminal_id),
            )
            await conn.execute(
                "DELETE FROM argus.snapshots WHERE source_url=%s",
                (source_url,),
            )
            await conn.execute(
                "DELETE FROM argus.worker_instances WHERE worker_id=%s",
                (stale_worker_id,),
            )
        await repository.close()
