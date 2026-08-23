import asyncio
import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from argus.config import Settings
from argus.contracts.models import CollectionRecord, CollectionRequest, CollectionStatus, utcnow
from argus.research.planner import HeuristicResearchPlanner
from argus.storage.postgres_migrations import run_postgres_migrations
from argus.worker import CollectionWorker


TERMINAL_STATUSES = {
    CollectionStatus.COMPLETED,
    CollectionStatus.PARTIAL,
    CollectionStatus.BLOCKED,
    CollectionStatus.FAILED,
    CollectionStatus.CANCELLED,
}


def postgres_dsn() -> str:
    value = os.getenv("ARGUS_TEST_POSTGRES_DSN", "").strip()
    if not value:
        pytest.skip("ARGUS_TEST_POSTGRES_DSN is not configured")
    return value


async def wait_for_terminal(worker: CollectionWorker, collection_id: str) -> CollectionRecord:
    deadline = asyncio.get_running_loop().time() + 10
    while asyncio.get_running_loop().time() < deadline:
        record = await worker.repository.get_collection(collection_id)
        if record is not None and record.status in TERMINAL_STATUSES:
            return record
        await asyncio.sleep(0.05)
    raise AssertionError(f"collection {collection_id} did not reach a terminal status")


@pytest.mark.asyncio
async def test_postgres_worker_claims_executes_and_releases(tmp_path: Path):
    dsn = postgres_dsn()
    await run_postgres_migrations(dsn)

    collection_id = f"worker-e2e-{uuid4()}"
    now = utcnow()
    record = CollectionRecord(
        collection_id=collection_id,
        request=CollectionRequest(
            consumer="worker-e2e",
            analysis_id=f"analysis-{uuid4()}",
            territory={"city": "Ижевск"},
            intents=["public_mentions"],
        ),
        status=CollectionStatus.QUEUED,
        stage="queued",
        created_at=now,
        updated_at=now,
    )

    settings = Settings(
        execution_role="worker",
        storage_backend="postgresql",
        database_dsn=dsn,
        token_file=tmp_path / "token",
        browser_serp_enabled=False,
        worker_concurrency=1,
        worker_poll_interval_seconds=0.05,
        worker_lease_seconds=15,
        worker_heartbeat_seconds=1,
        worker_health_max_age_seconds=5,
    )
    worker = CollectionWorker(settings)
    worker.orchestrator.planner = HeuristicResearchPlanner()

    run_task: asyncio.Task[None] | None = None
    try:
        await worker.start()
        await worker.repository.create_collection(record)
        assert await worker.repository.active_worker_count(max_age_seconds=5) >= 1

        run_task = asyncio.create_task(worker.run())
        finished = await wait_for_terminal(worker, collection_id)

        assert finished.status == CollectionStatus.FAILED
        assert finished.stage == "failed:no_sources"
        assert any(error.code == "NO_SOURCE_TASKS" for error in finished.errors)

        with psycopg.connect(dsn) as conn:
            lease = conn.execute(
                "SELECT worker_id FROM argus.collection_leases WHERE collection_id=%s",
                (collection_id,),
            ).fetchone()
        assert lease is None
    finally:
        worker.request_stop()
        if run_task is not None:
            await asyncio.wait_for(run_task, timeout=10)
        elif worker._started:
            await worker.stop()

    with psycopg.connect(dsn) as conn:
        worker_row = conn.execute(
            "SELECT worker_id FROM argus.worker_instances WHERE worker_id=%s",
            (worker.worker_id,),
        ).fetchone()
    assert worker_row is None
