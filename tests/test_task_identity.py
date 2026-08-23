from pathlib import Path

import pytest

from argus.contracts.models import CollectionRequest, CollectionStatus, Observation
from argus.history.snapshots import sha256_text
from argus.orchestrator.service import CollectionOrchestrator
from argus.research.planner import HeuristicResearchPlanner
from argus.sources.base import SourceResult, SourceTask
from argus.sources.registry import SourceRegistry
from argus.storage.sqlite import SQLiteRepository


class SameEndpointAdapter:
    source_id = "same_endpoint"
    intents = {"reviews"}

    async def discover(self, request):
        del request
        return [
            SourceTask(
                source_id=self.source_id,
                goal="first",
                url="https://api.example/search",
                task_key="same_endpoint:first",
            ),
            SourceTask(
                source_id=self.source_id,
                goal="second",
                url="https://api.example/search",
                task_key="same_endpoint:second",
            ),
        ]

    async def fetch(self, task):
        return task

    async def extract(self, task, fetched, request):
        del fetched
        text = task.goal
        return SourceResult(
            observations=[
                Observation(
                    collection_id=str(task.metadata["collection_id"]),
                    analysis_id=request.analysis_id,
                    consumer=request.consumer,
                    source=self.source_id,
                    source_kind="test",
                    url=f"https://example.com/{task.goal}",
                    entity_type="document",
                    text=text,
                    content_hash=sha256_text(text),
                )
            ]
        )

    async def normalize(self, result):
        return result

    async def health(self):
        return {"status": "ready"}


def test_source_task_keeps_url_identity_as_backward_compatible_default():
    task = SourceTask(source_id="web", goal="x", url="https://example.com")
    assert task.dedupe_key == "web:https://example.com"


def test_source_task_can_override_identity_for_one_endpoint():
    task = SourceTask(
        source_id="api",
        goal="x",
        url="https://api.example/search",
        task_key="api:query:1",
    )
    assert task.dedupe_key == "api:query:1"


def test_url_dedupe_merges_research_goal_context_without_duplicate_fetch_task():
    existing = [
        SourceTask(
            source_id="generic_web",
            goal="public_mentions",
            url="https://example.com/page",
            metadata={
                "research_goals": ["public_mentions"],
                "discovery_engines": ["engine-a"],
                "discovery_rank": 3,
            },
        )
    ]
    additions = [
        SourceTask(
            source_id="generic_web",
            goal="local_news",
            url="https://example.com/page",
            metadata={
                "research_goals": ["local_news"],
                "discovery_engines": ["engine-b"],
                "discovery_provider": "search-b",
                "discovery_rank": 1,
            },
        )
    ]

    merged = CollectionOrchestrator._merge_tasks(existing, additions, "collection-1")

    assert len(merged) == 1
    assert merged[0].metadata["research_goals"] == ["public_mentions", "local_news"]
    assert merged[0].metadata["discovery_engines"] == ["engine-a", "engine-b"]
    assert merged[0].metadata["discovery_provider"] == "search-b"
    assert merged[0].metadata["discovery_rank"] == 1


@pytest.mark.asyncio
async def test_orchestrator_processes_distinct_tasks_for_same_endpoint(tmp_path: Path):
    repo = SQLiteRepository(tmp_path / "db.sqlite")
    registry = SourceRegistry()
    registry.register(SameEndpointAdapter())
    orchestrator = CollectionOrchestrator(repo, registry, HeuristicResearchPlanner())
    await orchestrator.start()
    accepted = await orchestrator.submit(
        CollectionRequest(
            consumer="test",
            analysis_id="1",
            territory={"city": "Ижевск"},
            intents=["reviews"],
        )
    )
    await orchestrator._jobs[accepted.collection_id]
    record = await repo.get_collection(accepted.collection_id)
    observations = await repo.list_observations(accepted.collection_id)
    await orchestrator.shutdown()

    assert record is not None
    assert record.status == CollectionStatus.COMPLETED
    assert len(observations) == 2
    assert set(record.checkpoint["visited"]) == {
        "same_endpoint:first",
        "same_endpoint:second",
    }
