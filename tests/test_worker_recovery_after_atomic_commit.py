from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from types import SimpleNamespace
from uuid import uuid4

import psycopg
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
from argus.history.snapshots import sha256_text
from argus.orchestrator.atomic import AtomicCollectionOrchestrator
from argus.research.planner import HeuristicResearchPlanner
from argus.sources.base import SourceResult, SourceTask
from argus.sources.registry import SourceRegistry
from argus.storage.fenced_postgres import FencedPostgresRepository
from argus.storage.lease_fencing import lease_fence
from argus.storage.postgres_migrations import run_postgres_migrations


@dataclass
class FetchCounter:
    calls: int = 0


class RecoveryAfterCommitAdapter:
    source_id = "recovery_after_commit"
    intents = {"recovery_after_commit"}

    def __init__(self, counter: FetchCounter) -> None:
        self.counter = counter

    async def discover(self, request):
        del request
        return [
            SourceTask(
                source_id=self.source_id,
                goal="recovery_after_commit",
                url="https://example.com/recovery-after-commit",
            )
        ]

    async def fetch(self, task):
        self.counter.calls += 1
        return SimpleNamespace(
            blocked=False,
            final_url=task.url,
            text="fact-committed-before-crash",
            content_type="text/plain",
        )

    async def extract(self, task, fetched, request):
        collection_id = str(task.metadata["collection_id"])
        content_hash = sha256_text(fetched.text)
        observation = Observation(
            observation_id=f"recovery-after-commit-observation-{collection_id}",
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
            evidence_id=f"recovery-after-commit-evidence-{collection_id}",
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


class CrashAfterAtomicCommitRepository(FencedPostgresRepository):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.crash_after_next_commit = True

    async def commit_task_success(self, record, *, observations, evidence, snapshots):
        await super().commit_task_success(
            record,
            observations=observations,
            evidence=evidence,
            snapshots=snapshots,
        )
        if self.crash_after_next_commit:
            self.crash_after_next_commit = False
            raise asyncio.CancelledError()


def postgres_dsn() -> str:
    value = os.getenv("ARGUS_TEST_POSTGRES_DSN", "").strip()
    if not value:
        pytest.skip("ARGUS_TEST_POSTGRES_DSN is not configured")
    return value


def registry(counter: FetchCounter) -> SourceRegistry:
    value = SourceRegistry()
    value.register(RecoveryAfterCommitAdapter(counter))
    return value


@pytest.mark.asyncio
async def test_recovery_after_atomic_commit_finalizes_without_refetching():
    dsn = postgres_dsn()
    await run_postgres_migrations(dsn)

    first_repository = CrashAfterAtomicCommitRepository(
        dsn,
        min_size=1,
        max_size=2,
        timeout_seconds=10,
        max_waiting=4,
    )
    recovery_repository = FencedPostgresRepository(
        dsn,
        min_size=1,
        max_size=2,
        timeout_seconds=10,
        max_waiting=4,
    )
    counter = FetchCounter()
    first = AtomicCollectionOrchestrator(
        first_repository,
        registry(counter),
        HeuristicResearchPlanner(),
        auto_execute=False,
    )
    recovered = AtomicCollectionOrchestrator(
        recovery_repository,
        registry(counter),
        HeuristicResearchPlanner(),
        auto_execute=False,
    )

    collection_id = f"recovery-after-commit-{uuid4()}"
    worker_a = f"worker-a-{uuid4()}"
    worker_b = f"worker-b-{uuid4()}"
    timestamp = utcnow()
    record = CollectionRecord(
        collection_id=collection_id,
        request=CollectionRequest(
            consumer="recovery-after-commit-test",
            analysis_id=f"analysis-{uuid4()}",
            territory={"city": "Ижевск"},
            intents=["recovery_after_commit"],
        ),
        status=CollectionStatus.QUEUED,
        stage="queued",
        created_at=timestamp,
        updated_at=timestamp,
    )

    try:
        await first_repository.initialize()
        await recovery_repository.initialize()
        await first_repository.register_worker(worker_a, metadata={"test": True})
        await recovery_repository.register_worker(worker_b, metadata={"test": True})
        await first_repository.create_collection(record)
        assert await first_repository.claim_next_collection(
            worker_a,
            lease_seconds=30,
        ) == collection_id

        with lease_fence(collection_id, worker_a):
            with pytest.raises(asyncio.CancelledError):
                await first.execute(collection_id)

        interrupted = await first_repository.get_collection(collection_id)
        assert interrupted is not None
        assert interrupted.status == CollectionStatus.RUNNING
        assert interrupted.checkpoint["planning_complete"] is True
        assert interrupted.checkpoint["pending_tasks"] == []
        assert interrupted.checkpoint["visited"] == [
            "recovery_after_commit:https://example.com/recovery-after-commit"
        ]
        assert counter.calls == 1
        assert len(await first_repository.list_observations(collection_id)) == 1
        assert len(await first_repository.list_evidence(collection_id)) == 1

        async with recovery_repository._pool.connection() as conn:
            await conn.execute(
                """
                UPDATE argus.collection_leases
                SET lease_until=NOW() - INTERVAL '1 second'
                WHERE collection_id=%s AND worker_id=%s
                """,
                (collection_id, worker_a),
            )
        assert await recovery_repository.claim_next_collection(
            worker_b,
            lease_seconds=30,
        ) == collection_id

        with lease_fence(collection_id, worker_b):
            await recovered.execute(collection_id)

        completed = await recovery_repository.get_collection(collection_id)
        assert completed is not None
        assert completed.status == CollectionStatus.COMPLETED
        assert completed.progress_percent == 100
        assert completed.stage == "completed"
        assert completed.checkpoint["visited"] == interrupted.checkpoint["visited"]
        assert completed.checkpoint["pending_tasks"] == []
        assert counter.calls == 1
        observations = await recovery_repository.list_observations(collection_id)
        evidence = await recovery_repository.list_evidence(collection_id)
        assert len(observations) == 1
        assert len(evidence) == 1
        assert evidence[0].observation_id == observations[0].observation_id
    finally:
        await first_repository.unregister_worker(worker_a)
        await recovery_repository.unregister_worker(worker_b)
        with psycopg.connect(dsn) as conn:
            conn.execute(
                "DELETE FROM argus.collections WHERE collection_id=%s",
                (collection_id,),
            )
        await first_repository.close()
        await recovery_repository.close()
