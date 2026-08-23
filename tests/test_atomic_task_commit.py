from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

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
from argus.history.snapshots import SnapshotService, sha256_text
from argus.orchestrator.atomic import AtomicCollectionOrchestrator
from argus.research.planner import ResearchPlan
from argus.sources.base import SourceResult, SourceTask
from argus.sources.registry import SourceRegistry
from argus.storage.atomic_sqlite import AtomicSQLiteRepository


class EmptyPlanner:
    async def plan(self, request):
        del request
        return ResearchPlan()


class AtomicAdapter:
    source_id = "atomic_test"
    intents = {"atomic_test"}

    def __init__(self, snapshots: SnapshotService) -> None:
        self.snapshots = snapshots

    async def discover(self, request):
        del request
        return [
            SourceTask(
                source_id=self.source_id,
                goal="atomic_test",
                url="https://example.com/fact",
            )
        ]

    async def fetch(self, task):
        return SimpleNamespace(
            blocked=False,
            final_url=task.url,
            text="fact-v1",
            content_type="text/plain",
        )

    async def extract(self, task, fetched, request):
        collection_id = str(task.metadata["collection_id"])
        snapshot = await self.snapshots.capture(
            self.source_id,
            fetched.final_url,
            fetched.text,
            fetched.content_type,
            collection_id=collection_id,
        )
        content_hash = sha256_text(fetched.text)
        observation = Observation(
            observation_id=f"obs-{collection_id}",
            collection_id=collection_id,
            analysis_id=request.analysis_id,
            consumer=request.consumer,
            source=self.source_id,
            source_kind="test",
            url=fetched.final_url,
            entity_type="document",
            text=fetched.text,
            content_hash=content_hash,
            provenance={"snapshot_id": snapshot.snapshot_id},
        )
        evidence = Evidence(
            evidence_id=f"evidence-{collection_id}",
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


class FailFirstCommitRepository(AtomicSQLiteRepository):
    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.fail_next_commit = True

    async def commit_task_success(self, record, *, observations, evidence, snapshots):
        if self.fail_next_commit:
            self.fail_next_commit = False
            raise asyncio.CancelledError()
        await super().commit_task_success(
            record,
            observations=observations,
            evidence=evidence,
            snapshots=snapshots,
        )


def request() -> CollectionRequest:
    return CollectionRequest(
        consumer="atomic-test",
        analysis_id="analysis-atomic",
        territory={"city": "Ижевск"},
        intents=["atomic_test"],
        constraints={"max_pages": 5},
    )


def build_orchestrator(repository):
    snapshots = SnapshotService(repository)
    registry = SourceRegistry()
    registry.register(AtomicAdapter(snapshots))
    return AtomicCollectionOrchestrator(
        repository,
        registry,
        EmptyPlanner(),
        auto_execute=False,
    )


@pytest.mark.asyncio
async def test_failed_atomic_commit_leaves_task_replayable_and_snapshot_unpublished(tmp_path: Path):
    repository = FailFirstCommitRepository(tmp_path / "argus.sqlite")
    orchestrator = build_orchestrator(repository)
    await orchestrator.start()
    accepted = await orchestrator.submit(request())

    with pytest.raises(asyncio.CancelledError):
        await orchestrator.execute(accepted.collection_id)

    interrupted = await repository.get_collection(accepted.collection_id)
    assert interrupted is not None
    assert interrupted.status == CollectionStatus.RUNNING
    assert interrupted.checkpoint.get("visited", []) == []
    assert interrupted.checkpoint.get("pending_tasks")
    assert await repository.list_observations(accepted.collection_id) == []
    assert await repository.list_evidence(accepted.collection_id) == []
    assert await repository.latest_snapshot("https://example.com/fact") is None

    await orchestrator.execute(accepted.collection_id)

    completed = await repository.get_collection(accepted.collection_id)
    assert completed is not None
    assert completed.status == CollectionStatus.COMPLETED
    assert completed.checkpoint["visited"] == [
        "atomic_test:https://example.com/fact"
    ]
    observations = await repository.list_observations(accepted.collection_id)
    evidence = await repository.list_evidence(accepted.collection_id)
    snapshot = await repository.latest_snapshot("https://example.com/fact")
    assert len(observations) == 1
    assert len(evidence) == 1
    assert snapshot is not None
    assert observations[0].provenance["snapshot_id"] == snapshot.snapshot_id


@pytest.mark.asyncio
async def test_sqlite_task_commit_is_one_transaction(tmp_path: Path):
    repository = AtomicSQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    collection_id = "atomic-transaction"
    created_at = utcnow()
    original = CollectionRecord(
        collection_id=collection_id,
        request=request(),
        status=CollectionStatus.RUNNING,
        created_at=created_at,
        updated_at=created_at,
        checkpoint={"pending_tasks": [{"source_id": "atomic_test"}]},
    )
    await repository.create_collection(original)

    observation = Observation(
        observation_id="atomic-observation",
        collection_id=collection_id,
        analysis_id=original.request.analysis_id,
        consumer=original.request.consumer,
        source="atomic_test",
        source_kind="test",
        url="https://example.com/fact",
        entity_type="document",
        text="fact",
        content_hash=sha256_text("fact"),
    )
    evidence = Evidence(
        evidence_id="atomic-evidence",
        observation_id=observation.observation_id,
        type="test",
        text="fact",
        source=EvidenceSource(
            provider="atomic_test",
            url=observation.url,
            collected_at=observation.collected_at,
            source_id="atomic_test",
        ),
    )
    snapshot = Snapshot(
        snapshot_id="atomic-snapshot",
        source_id="atomic_test",
        source_url=observation.url,
        content_hash=sha256_text("fact"),
        extractor_version="test",
        content="fact",
    )
    committed = original.model_copy(deep=True)
    committed.checkpoint = {"visited": ["atomic_test:https://example.com/fact"]}
    committed.updated_at = utcnow()

    await repository.commit_task_success(
        committed,
        observations=[observation],
        evidence=[evidence],
        snapshots=[snapshot],
    )

    loaded = await repository.get_collection(collection_id)
    assert loaded is not None
    assert loaded.checkpoint == committed.checkpoint
    assert [item.observation_id for item in await repository.list_observations(collection_id)] == [
        observation.observation_id
    ]
    assert [item.evidence_id for item in await repository.list_evidence(collection_id)] == [
        evidence.evidence_id
    ]
    latest = await repository.latest_snapshot(observation.url)
    assert latest is not None
    assert latest.snapshot_id == snapshot.snapshot_id
