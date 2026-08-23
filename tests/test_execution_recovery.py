import asyncio
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.contracts.models import (
    CollectionRequest,
    CollectionStatus,
    Evidence,
    EvidenceSource,
    Observation,
)
from argus.history.snapshots import SnapshotService, sha256_text
from argus.normalization.identity import stable_evidence_id, stable_observation_id
from argus.orchestrator.service import CollectionOrchestrator
from argus.research.planner import HeuristicResearchPlanner
from argus.sources.base import SourceResult, SourceTask
from argus.sources.registry import SourceRegistry
from argus.storage.sqlite import SQLiteRepository


class CrashBeforeCheckpointRepository(SQLiteRepository):
    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.crash_enabled = True

    async def update_collection(self, record):
        visited = record.checkpoint.get("visited", [])
        if (
            self.crash_enabled
            and record.stage == "collecting:crashy"
            and "crashy:https://example.com/page" in visited
        ):
            self.crash_enabled = False
            raise asyncio.CancelledError()
        await super().update_collection(record)


class CrashReplayAdapter:
    source_id = "crashy"
    intents = {"reviews"}

    def __init__(self, snapshots: SnapshotService) -> None:
        self.snapshots = snapshots
        self.extract_calls = 0

    async def discover(self, request):
        del request
        return [
            SourceTask(
                source_id=self.source_id,
                goal="reviews",
                url="https://example.com/page",
            )
        ]

    async def fetch(self, task):
        return SimpleNamespace(
            final_url=task.url,
            text="stable factual content",
            content_type="text/html",
        )

    async def extract(self, task, fetched, request):
        self.extract_calls += 1
        collection_id = str(task.metadata["collection_id"])
        snapshot = await self.snapshots.capture(
            self.source_id,
            fetched.final_url,
            fetched.text,
            fetched.content_type,
            collection_id=collection_id,
        )
        content_hash = sha256_text(fetched.text)
        observation_id = stable_observation_id(
            collection_id=collection_id,
            source_id=self.source_id,
            entity_type="document",
            source_url=fetched.final_url,
            content_hash=content_hash,
        )
        observation = Observation(
            observation_id=observation_id,
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
            quality={"evidence_backed": True},
        )
        evidence = Evidence(
            evidence_id=stable_evidence_id(
                observation_id=observation_id,
                evidence_type="document",
                source_url=fetched.final_url,
                text=fetched.text,
            ),
            observation_id=observation_id,
            type="document",
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


def build_registry(adapter: CrashReplayAdapter) -> SourceRegistry:
    registry = SourceRegistry()
    registry.register(adapter)
    return registry


@pytest.mark.asyncio
async def test_crash_after_facts_before_checkpoint_replays_without_duplicates(tmp_path: Path):
    db_path = tmp_path / "argus.sqlite"
    first_repo = CrashBeforeCheckpointRepository(db_path)
    first_snapshots = SnapshotService(first_repo)
    first_adapter = CrashReplayAdapter(first_snapshots)
    first = CollectionOrchestrator(
        first_repo,
        build_registry(first_adapter),
        HeuristicResearchPlanner(),
    )
    await first.start()
    accepted = await first.submit(
        CollectionRequest(
            consumer="recovery-test",
            analysis_id="analysis-1",
            territory={"city": "Ижевск"},
            intents=["reviews"],
        )
    )

    with pytest.raises(asyncio.CancelledError):
        await first._jobs[accepted.collection_id]

    interrupted = await first_repo.get_collection(accepted.collection_id)
    assert interrupted is not None
    assert interrupted.status == CollectionStatus.RUNNING
    assert "crashy:https://example.com/page" not in interrupted.checkpoint.get("visited", [])
    assert len(await first_repo.list_observations(accepted.collection_id)) == 1
    assert len(await first_repo.list_evidence(accepted.collection_id)) == 1
    await first.shutdown()

    second_repo = SQLiteRepository(db_path)
    second_snapshots = SnapshotService(second_repo)
    second_adapter = CrashReplayAdapter(second_snapshots)
    recovered = CollectionOrchestrator(
        second_repo,
        build_registry(second_adapter),
        HeuristicResearchPlanner(),
    )
    await recovered.start()
    await recovered._jobs[accepted.collection_id]

    record = await second_repo.get_collection(accepted.collection_id)
    observations = await second_repo.list_observations(accepted.collection_id)
    evidence = await second_repo.list_evidence(accepted.collection_id)
    with sqlite3.connect(db_path) as conn:
        snapshot_count = int(conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0])
    await recovered.shutdown()

    assert record is not None
    assert record.status == CollectionStatus.COMPLETED
    assert second_adapter.extract_calls == 1
    assert len(observations) == 1
    assert len(evidence) == 1
    assert snapshot_count == 1
    assert observations[0].provenance["snapshot_id"].startswith("snapshot-")
