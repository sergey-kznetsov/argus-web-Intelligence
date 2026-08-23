from pathlib import Path

import pytest

from argus.contracts.models import CollectionRequest, CollectionStatus
from argus.orchestrator.service import CollectionOrchestrator
from argus.research.planner import HeuristicResearchPlanner
from argus.sources.registry import SourceRegistry
from argus.storage.sqlite import SQLiteRepository


@pytest.mark.asyncio
async def test_queue_only_orchestrator_does_not_execute_submit(tmp_path: Path):
    repository = SQLiteRepository(tmp_path / "argus.sqlite")
    orchestrator = CollectionOrchestrator(
        repository,
        SourceRegistry(),
        HeuristicResearchPlanner(),
        auto_execute=False,
    )
    await orchestrator.start()
    accepted = await orchestrator.submit(
        CollectionRequest(
            consumer="server-api",
            analysis_id="queue-only",
            territory={"city": "Ижевск"},
            intents=["public_mentions"],
        )
    )

    record = await repository.get_collection(accepted.collection_id)
    assert record is not None
    assert record.status == CollectionStatus.QUEUED
    assert record.stage == "queued"
    assert orchestrator._jobs == {}

    await orchestrator.shutdown()
    await repository.close()


@pytest.mark.asyncio
async def test_manual_execute_runs_persisted_queue_item(tmp_path: Path):
    repository = SQLiteRepository(tmp_path / "argus.sqlite")
    orchestrator = CollectionOrchestrator(
        repository,
        SourceRegistry(),
        HeuristicResearchPlanner(),
        auto_execute=False,
    )
    await orchestrator.start()
    accepted = await orchestrator.submit(
        CollectionRequest(
            consumer="worker",
            analysis_id="manual-execute",
            territory={"city": "Ижевск"},
            intents=["public_mentions"],
        )
    )

    await orchestrator.execute(accepted.collection_id)
    record = await repository.get_collection(accepted.collection_id)
    assert record is not None
    assert record.status == CollectionStatus.FAILED
    assert any(error.code == "NO_SOURCE_TASKS" for error in record.errors)

    await orchestrator.shutdown()
    await repository.close()
