import asyncio
import os
from pathlib import Path
from uuid import uuid4

import pytest

from argus.config import Settings
from argus.contracts.models import CollectionRecord, CollectionRequest, CollectionStatus, utcnow
from argus.llm_health import LlmHealth
from argus.research.planner import HeuristicResearchPlanner
from argus.storage.postgres_migrations import run_postgres_migrations
from argus.worker import CollectionWorker


class MutableLlmHealth:
    def __init__(self, *, ready: bool = True) -> None:
        self.ready = ready
        self.calls = 0

    async def check(self, *, force: bool = False) -> LlmHealth:
        self.calls += 1
        return LlmHealth(
            status="ok" if self.ready else "unavailable",
            ready=self.ready,
            backend="ollama",
            model="qwen3:8b",
            reason_code=None if self.ready else "OLLAMA_UNAVAILABLE",
        )


def postgres_dsn() -> str:
    value = os.getenv("ARGUS_TEST_POSTGRES_DSN", "").strip()
    if not value:
        pytest.skip("ARGUS_TEST_POSTGRES_DSN is not configured")
    return value


async def wait_until_not_queued(worker: CollectionWorker, collection_id: str) -> CollectionRecord:
    deadline = asyncio.get_running_loop().time() + 10
    while asyncio.get_running_loop().time() < deadline:
        record = await worker.repository.get_collection(collection_id)
        if record is not None and record.status != CollectionStatus.QUEUED:
            return record
        await asyncio.sleep(0.05)
    raise AssertionError("collection remained queued after required LLM recovered")


@pytest.mark.asyncio
async def test_worker_pauses_new_claims_until_required_llm_recovers(tmp_path: Path):
    dsn = postgres_dsn()
    await run_postgres_migrations(dsn)

    settings = Settings(
        execution_role="worker",
        storage_backend="postgresql",
        database_dsn=dsn,
        token_file=tmp_path / "token",
        browser_serp_enabled=False,
        llm_required=True,
        worker_concurrency=1,
        worker_poll_interval_seconds=0.05,
        worker_lease_seconds=15,
        worker_heartbeat_seconds=1,
    )
    worker = CollectionWorker(settings)
    health = MutableLlmHealth(ready=True)
    worker.services.llm_health = health
    worker.orchestrator.planner = HeuristicResearchPlanner()

    collection_id = f"llm-gate-{uuid4()}"
    now = utcnow()
    record = CollectionRecord(
        collection_id=collection_id,
        request=CollectionRequest(
            consumer="llm-gate-test",
            analysis_id=f"analysis-{uuid4()}",
            territory={"city": "Пермь"},
            intents=["public_mentions"],
        ),
        status=CollectionStatus.QUEUED,
        stage="queued",
        created_at=now,
        updated_at=now,
    )

    run_task: asyncio.Task[None] | None = None
    try:
        await worker.start()
        await worker.repository.create_collection(record)

        health.ready = False
        run_task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.2)

        paused = await worker.repository.get_collection(collection_id)
        assert paused is not None
        assert paused.status == CollectionStatus.QUEUED
        assert worker._llm_claims_ready is False

        health.ready = True
        resumed = await wait_until_not_queued(worker, collection_id)
        assert resumed.status in {CollectionStatus.RUNNING, CollectionStatus.FAILED}
        assert worker._llm_claims_ready is True
        assert health.calls >= 3
    finally:
        worker.request_stop()
        if run_task is not None:
            await asyncio.wait_for(run_task, timeout=10)
        elif worker._started:
            await worker.stop()
