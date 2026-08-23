from __future__ import annotations

import asyncio
import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from argus.config import Settings
from argus.contracts.models import CollectionRecord, CollectionRequest, CollectionStatus, utcnow
from argus.storage.postgres_migrations import run_postgres_migrations
from argus.worker import CollectionWorker


class BlockingOrchestrator:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def execute(self, collection_id: str) -> None:
        del collection_id
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


def postgres_dsn() -> str:
    value = os.getenv("ARGUS_TEST_POSTGRES_DSN", "").strip()
    if not value:
        pytest.skip("ARGUS_TEST_POSTGRES_DSN is not configured")
    return value


def worker_settings(dsn: str, token_file: Path) -> Settings:
    return Settings(
        execution_role="worker",
        storage_backend="postgresql",
        database_dsn=dsn,
        token_file=token_file,
        browser_serp_enabled=False,
        worker_concurrency=1,
        worker_poll_interval_seconds=0.05,
        worker_lease_seconds=15,
        worker_heartbeat_seconds=1,
        worker_health_max_age_seconds=5,
    )


def collection(collection_id: str) -> CollectionRecord:
    timestamp = utcnow()
    return CollectionRecord(
        collection_id=collection_id,
        request=CollectionRequest(
            consumer="database-lease-failure-test",
            analysis_id=f"analysis-{uuid4()}",
            territory={"city": "Ижевск"},
            intents=["public_mentions"],
        ),
        status=CollectionStatus.QUEUED,
        stage="queued",
        created_at=timestamp,
        updated_at=timestamp,
    )


@pytest.mark.asyncio
async def test_database_error_during_lease_heartbeat_cancels_active_execution(tmp_path: Path):
    dsn = postgres_dsn()
    await run_postgres_migrations(dsn)
    worker_a = CollectionWorker(worker_settings(dsn, tmp_path / "db-loss-token-a"))
    worker_b = CollectionWorker(worker_settings(dsn, tmp_path / "db-loss-token-b"))
    blocking = BlockingOrchestrator()
    worker_a.orchestrator = blocking

    collection_id = f"database-lease-failure-{uuid4()}"
    execution: asyncio.Task[None] | None = None
    try:
        await worker_a.start()
        await worker_b.start()
        await worker_a.repository.create_collection(collection(collection_id))
        assert await worker_a.repository.claim_next_collection(
            worker_a.worker_id,
            lease_seconds=worker_a.settings.worker_lease_seconds,
        ) == collection_id

        async def failed_renew(*args, **kwargs):
            del args, kwargs
            raise psycopg.OperationalError("simulated PostgreSQL heartbeat outage")

        async def failed_release(*args, **kwargs):
            del args, kwargs
            raise psycopg.OperationalError("simulated PostgreSQL release outage")

        worker_a.repository.renew_collection_lease = failed_renew  # type: ignore[method-assign]
        worker_a.repository.release_collection_lease = failed_release  # type: ignore[method-assign]
        execution = asyncio.create_task(worker_a._execute_claim(collection_id))
        await asyncio.wait_for(blocking.started.wait(), timeout=5)
        result = await asyncio.wait_for(
            asyncio.gather(execution, return_exceptions=True),
            timeout=5,
        )

        assert len(result) == 1
        assert isinstance(result[0], psycopg.OperationalError)
        await asyncio.wait_for(blocking.cancelled.wait(), timeout=2)
        assert await worker_a.repository.list_observations(collection_id) == []
        assert await worker_a.repository.list_evidence(collection_id) == []

        # During a real outage the release attempt can fail too. The old lease remains
        # authoritative until expiry, so another worker cannot publish concurrently.
        assert await worker_b.repository.claim_next_collection(
            worker_b.worker_id,
            lease_seconds=15,
        ) is None

        async with worker_b.repository._pool.connection() as conn:
            await conn.execute(
                """
                UPDATE argus.collection_leases
                SET lease_until=NOW() - INTERVAL '1 second'
                WHERE collection_id=%s AND worker_id=%s
                """,
                (collection_id, worker_a.worker_id),
            )
        assert await worker_b.repository.claim_next_collection(
            worker_b.worker_id,
            lease_seconds=15,
        ) == collection_id
    finally:
        if execution is not None and not execution.done():
            execution.cancel()
            await asyncio.gather(execution, return_exceptions=True)
        if worker_a._started:
            await worker_a.stop()
        if worker_b._started:
            await worker_b.stop()
        with psycopg.connect(dsn) as conn:
            conn.execute(
                "DELETE FROM argus.collections WHERE collection_id=%s",
                (collection_id,),
            )
