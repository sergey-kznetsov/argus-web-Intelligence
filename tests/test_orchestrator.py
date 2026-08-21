from pathlib import Path

import pytest

from argus.contracts.models import CollectionRequest, CollectionStatus
from argus.orchestrator.service import CollectionOrchestrator
from argus.research.planner import HeuristicResearchPlanner
from argus.sources.registry import SourceRegistry
from argus.storage.sqlite import SQLiteRepository


@pytest.mark.asyncio
async def test_empty_collection_completes(tmp_path: Path):
    repo = SQLiteRepository(tmp_path / "db.sqlite")
    orchestrator = CollectionOrchestrator(repo, SourceRegistry(), HeuristicResearchPlanner())
    await orchestrator.start()
    accepted = await orchestrator.submit(CollectionRequest(
        consumer="test", analysis_id="1", territory={"city": "Ижевск"}, intents=["reviews"]
    ))
    task = orchestrator._jobs[accepted.collection_id]
    await task
    record = await repo.get_collection(accepted.collection_id)
    assert record and record.status == CollectionStatus.COMPLETED
