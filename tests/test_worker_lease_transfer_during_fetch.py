from __future__ import annotations

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest

from argus.config import Settings
from argus.contracts.models import (
    CollectionRecord,
    CollectionRequest,
    CollectionStatus,
    Evidence,
    EvidenceSource,
    Observation,
    utcnow,
)
from argus.history.snapshots import sha256_text
from argus.research.planner import HeuristicResearchPlanner
from argus.sources.base import SourceResult, SourceTask
from argus.storage.postgres_migrations import run_postgres_migrations
from argus.worker import CollectionWorker


class LeaseTransferFetchAdapter:
    source_id = "lease_transfer_fetch"
    intents = {"lease_transfer_fetch"}

    def __init__(self, *, block_fetch: bool) -> None:
        self.block_fetch = block_fetch
        self.fetch_started = asyncio.Event()
        self.fetch_cancelled = asyncio.Event()

    async def discover(self, request):
        del request
        return [
            SourceTask(
                source_id=self.source_id,
                goal="lease_transfer_fetch",
                url="https://example.com/lease-transfer",
            )
        ]

    async def fetch(self, task):
        self.fetch_started.set()
        if self.block_fetch:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.fetch_cancelled.set()
                raise
        return SimpleNamespace(
            blocked=False,
            final_url=task.url,
            text="lease-transfer-fact",
            content_type="text/plain",
        )

    async def extract(self, task, fetched, request):
        collection_id = str(task.metadata["collection_id"])
        content_hash = sha256_text(fetched.text)
        observation = Observation(
            observation_id=f"lease-transfer-observation-{collection_id}",
            collection_id=collection_id,
            analysis_id=request.analysis_id,
            consumer=request.consumer,
            source=self.source_id,
            source_kind="test",
            url=fetched.final_url,
            entity_type="document",
            text=fetched.text,
            content_hash=content_hash,
        )
        evidence = Evidence(
            evidence_id=f"lease-transfer-evidence-{collection_id}",
            observation_id=observation.observation_id,
            type="test",
            text=fetched.text,
            source=EvidenceSource(
                provider=self.source_id,
                url=fetched.final_url,
                collected_at=observation.collected_at,
                source_id=self.source_id,
            ),
        )
        return SourceResult(observations=[observation], evidence=[evidence])

    async def normalize(self, result):
        return result

    async def health(self):
        return {"status": "ok"}


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
            consumer="lease-transfer-test",
            analysis_id=f"analysis-{uuid4()}",
            territory={"city": "Ижевск"},
            intents=["lease_transfer_fetch"],
        ),
        status=CollectionStatus.QUEUED,
        stage="queued",
        created_at=timestamp,
        updated_at=timestamp,
    )


async def claim_until(
    worker: CollectionWorker,
    collection_id: str,
    *,
    timeout: float = 5.0,
) -> str:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        claimed = await worker.repository.claim_next_collection(
            worker.worker_id,
            lease_seconds=worker.settings.worker_lease_seconds,
        )
        if claimed == collection_id:
            return claimed
        if claimed is not None:
            await worker.repository.release_collection_lease(claimed, worker.worker_id)
        await asyncio.sleep(0.02)
    raise AssertionError(f"worker did not claim {collection_id} before timeout")


@pytest.mark.asyncio
async def test_lease_transfer_during_fetch_cancels_stale_worker_and_avoids_duplicates(
    tmp_path: Path,
):
    dsn = postgres_dsn()
    await run_postgres_migrations(dsn)
    worker_a = CollectionWorker(worker_settings(dsn, tmp_path / "token-a"))
    worker_b = CollectionWorker(worker_settings(dsn, tmp_path / "token-b"))
    blocked_adapter = LeaseTransferFetchAdapter(block_fetch=True)
    recovery_adapter = LeaseTransferFetchAdapter(block_fetch=False)
    worker_a.services.registry.register(blocked_adapter)
    worker_b.services.registry.register(recovery_adapter)
    worker_a.orchestrator.planner = HeuristicResearchPlanner()
    worker_b.orchestrator.planner = HeuristicResearchPlanner()

    collection_id = f"lease-transfer-{uuid4()}"
    stale_task: asyncio.Task[None] | None = None
    try:
        await worker_a.start()
        await worker_b.start()
        await worker_a.repository.create_collection(collection(collection_id))
        assert await worker_a.repository.claim_next_collection(
            worker_a.worker_id,
            lease_seconds=15,
        ) == collection_id

        stale_task = asyncio.create_task(worker_a._execute_claim(collection_id))
        await asyncio.wait_for(blocked_adapter.fetch_started.wait(), timeout=5)

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

        await asyncio.wait_for(worker_b._execute_claim(collection_id), timeout=10)
        await asyncio.wait_for(blocked_adapter.fetch_cancelled.wait(), timeout=5)
        await asyncio.gather(stale_task, return_exceptions=True)

        stored = await worker_b.repository.get_collection(collection_id)
        assert stored is not None
        assert stored.status == CollectionStatus.COMPLETED
        observations = await worker_b.repository.list_observations(collection_id)
        evidence = await worker_b.repository.list_evidence(collection_id)
        assert len(observations) == 1
        assert len(evidence) == 1
        assert observations[0].observation_id == (
            f"lease-transfer-observation-{collection_id}"
        )
        assert evidence[0].observation_id == observations[0].observation_id
        assert recovery_adapter.fetch_started.is_set()
    finally:
        if stale_task is not None and not stale_task.done():
            stale_task.cancel()
            await asyncio.gather(stale_task, return_exceptions=True)
        if worker_a._started:
            await worker_a.stop()
        if worker_b._started:
            await worker_b.stop()
        with psycopg.connect(dsn) as conn:
            conn.execute(
                "DELETE FROM argus.collections WHERE collection_id=%s",
                (collection_id,),
            )


@pytest.mark.asyncio
async def test_graceful_shutdown_releases_active_lease_for_immediate_handoff(tmp_path: Path):
    dsn = postgres_dsn()
    await run_postgres_migrations(dsn)
    worker_a = CollectionWorker(worker_settings(dsn, tmp_path / "shutdown-token-a"))
    worker_b = CollectionWorker(worker_settings(dsn, tmp_path / "shutdown-token-b"))
    blocked_adapter = LeaseTransferFetchAdapter(block_fetch=True)
    recovery_adapter = LeaseTransferFetchAdapter(block_fetch=False)
    worker_a.services.registry.register(blocked_adapter)
    worker_b.services.registry.register(recovery_adapter)
    worker_a.orchestrator.planner = HeuristicResearchPlanner()
    worker_b.orchestrator.planner = HeuristicResearchPlanner()

    collection_id = f"shutdown-handoff-{uuid4()}"
    run_task: asyncio.Task[None] | None = None
    claim_task: asyncio.Task[str] | None = None
    try:
        await worker_a.start()
        await worker_b.start()
        await worker_a.repository.create_collection(collection(collection_id))

        run_task = asyncio.create_task(worker_a.run())
        await asyncio.wait_for(blocked_adapter.fetch_started.wait(), timeout=5)

        claim_task = asyncio.create_task(claim_until(worker_b, collection_id, timeout=5))
        worker_a.request_stop()
        await asyncio.wait_for(run_task, timeout=5)
        assert await asyncio.wait_for(claim_task, timeout=5) == collection_id
        await asyncio.wait_for(blocked_adapter.fetch_cancelled.wait(), timeout=5)

        with psycopg.connect(dsn) as conn:
            old_worker = conn.execute(
                "SELECT worker_id FROM argus.worker_instances WHERE worker_id=%s",
                (worker_a.worker_id,),
            ).fetchone()
            lease_owner = conn.execute(
                "SELECT worker_id FROM argus.collection_leases WHERE collection_id=%s",
                (collection_id,),
            ).fetchone()
        assert old_worker is None
        assert lease_owner is not None
        assert lease_owner[0] == worker_b.worker_id

        await asyncio.wait_for(worker_b._execute_claim(collection_id), timeout=10)
        stored = await worker_b.repository.get_collection(collection_id)
        assert stored is not None
        assert stored.status == CollectionStatus.COMPLETED
        observations = await worker_b.repository.list_observations(collection_id)
        evidence = await worker_b.repository.list_evidence(collection_id)
        assert len(observations) == 1
        assert len(evidence) == 1
        assert recovery_adapter.fetch_started.is_set()
    finally:
        if claim_task is not None and not claim_task.done():
            claim_task.cancel()
            await asyncio.gather(claim_task, return_exceptions=True)
        if run_task is not None and not run_task.done():
            worker_a.request_stop()
            await asyncio.gather(run_task, return_exceptions=True)
        if worker_a._started:
            await worker_a.stop()
        if worker_b._started:
            await worker_b.stop()
        with psycopg.connect(dsn) as conn:
            conn.execute(
                "DELETE FROM argus.collections WHERE collection_id=%s",
                (collection_id,),
            )
