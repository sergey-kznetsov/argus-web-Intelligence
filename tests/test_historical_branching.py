from pathlib import Path

import pytest

from argus.contracts.models import CollectionRequest, CollectionStatus, Observation
from argus.history.snapshots import sha256_text
from argus.orchestrator.service import CollectionOrchestrator
from argus.research.discovery import DiscoveryOutcome
from argus.research.historical import HistoricalBranchPlanner
from argus.research.planner import HeuristicResearchPlanner
from argus.sources.base import SourceResult, SourceTask
from argus.sources.registry import SourceRegistry
from argus.storage.sqlite import SQLiteRepository


class HistoricalSeedAdapter:
    source_id = "historical_seed"
    intents = {"historical_context"}

    async def discover(self, request):
        del request
        return [
            SourceTask(
                source_id=self.source_id,
                goal="historical_context",
                url="https://example.com/seed",
            )
        ]

    async def fetch(self, task):
        return task

    async def extract(self, task, fetched, request):
        del fetched
        text = "Дом купца Иванова"
        return SourceResult(
            observations=[
                Observation(
                    collection_id=str(task.metadata["collection_id"]),
                    analysis_id=request.analysis_id,
                    consumer=request.consumer,
                    source=self.source_id,
                    source_kind="historical_test",
                    url=task.url,
                    entity_type="document",
                    title=text,
                    text=text,
                    content_hash=sha256_text(text),
                )
            ]
        )

    async def normalize(self, result):
        return result

    async def health(self):
        return {"status": "ready"}


class HistoricalBranchAdapter:
    source_id = "historical_branch_test"
    intents = {"branch_only"}

    async def discover(self, request):
        del request
        return []

    async def fetch(self, task):
        return task

    async def extract(self, task, fetched, request):
        del fetched
        text = "Архивная публикация"
        return SourceResult(
            observations=[
                Observation(
                    collection_id=str(task.metadata["collection_id"]),
                    analysis_id=request.analysis_id,
                    consumer=request.consumer,
                    source=self.source_id,
                    source_kind="historical_test",
                    url=task.url,
                    entity_type="document",
                    title=text,
                    text=text,
                    content_hash=sha256_text(text),
                )
            ]
        )

    async def normalize(self, result):
        return result

    async def health(self):
        return {"status": "ready"}


class BranchDiscovery:
    def __init__(self, *, return_task: bool = True) -> None:
        self.return_task = return_task
        self.calls: list[list[str]] = []

    async def discover(self, queries, request):
        assert request.intents == ["historical_context"]
        self.calls.append(list(queries))
        tasks = []
        if self.return_task:
            tasks = [
                SourceTask(
                    source_id="historical_branch_test",
                    goal="historical_context",
                    url="https://archive.example/item",
                )
            ]
        return DiscoveryOutcome(tasks=tasks, providers_attempted=["fake"])


@pytest.mark.asyncio
async def test_historical_observation_triggers_bounded_recursive_discovery(tmp_path: Path):
    repo = SQLiteRepository(tmp_path / "db.sqlite")
    registry = SourceRegistry()
    registry.register(HistoricalSeedAdapter())
    registry.register(HistoricalBranchAdapter())
    discovery = BranchDiscovery()
    orchestrator = CollectionOrchestrator(
        repo,
        registry,
        HeuristicResearchPlanner(),
        discovery=discovery,
        historical_branch_planner=HistoricalBranchPlanner(),
    )
    await orchestrator.start()
    accepted = await orchestrator.submit(
        CollectionRequest(
            consumer="test",
            analysis_id="analysis-1",
            territory={"city": "Ижевск", "address": "Советская, 1"},
            intents=["historical_context"],
            constraints={"max_depth": 1, "max_pages": 10, "language": "ru"},
        )
    )
    await orchestrator._jobs[accepted.collection_id]
    record = await repo.get_collection(accepted.collection_id)
    observations = await repo.list_observations(accepted.collection_id)
    await orchestrator.shutdown()

    assert record is not None
    assert record.status == CollectionStatus.COMPLETED
    assert len(observations) == 2
    assert len(discovery.calls) == 1
    assert len(discovery.calls[0]) <= 3
    assert any("Дом купца Иванова" in query for query in discovery.calls[0])
    assert record.checkpoint["historical_branch_queries"] == sorted(discovery.calls[0])


@pytest.mark.asyncio
async def test_empty_historical_branch_is_terminal_not_partial(tmp_path: Path):
    repo = SQLiteRepository(tmp_path / "db.sqlite")
    registry = SourceRegistry()
    registry.register(HistoricalSeedAdapter())
    discovery = BranchDiscovery(return_task=False)
    orchestrator = CollectionOrchestrator(
        repo,
        registry,
        HeuristicResearchPlanner(),
        discovery=discovery,
        historical_branch_planner=HistoricalBranchPlanner(),
    )
    await orchestrator.start()
    accepted = await orchestrator.submit(
        CollectionRequest(
            consumer="test",
            analysis_id="analysis-2",
            territory={"city": "Ижевск"},
            intents=["historical_context"],
            constraints={"max_depth": 2, "max_pages": 10, "language": "ru"},
        )
    )
    await orchestrator._jobs[accepted.collection_id]
    record = await repo.get_collection(accepted.collection_id)
    await orchestrator.shutdown()

    assert record is not None
    assert record.status == CollectionStatus.COMPLETED
    assert len(discovery.calls) == 1
    assert not any(error.code == "DISCOVERY_NO_RESULTS" for error in record.errors)
