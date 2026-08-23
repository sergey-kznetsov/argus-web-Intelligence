from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.contracts.models import CollectionRequest, CollectionStatus, Observation
from argus.history.snapshots import sha256_text
from argus.orchestrator.service import CollectionOrchestrator
from argus.research.discovery import DiscoveryOutcome, DiscoveryService
from argus.research.planner import HeuristicResearchPlanner
from argus.sources.base import SourceResult, SourceTask
from argus.sources.registry import SourceRegistry
from argus.storage.sqlite import SQLiteRepository


class CoveringAdapter:
    source_id = "covering"
    intents = {"school"}

    async def discover(self, request):
        if "school" not in request.intents:
            return []
        return [SourceTask(source_id=self.source_id, goal="school", url="https://example.com/map")]

    async def fetch(self, task):
        return SimpleNamespace(url=task.url)

    async def extract(self, task, fetched, request):
        del fetched
        return SourceResult(
            observations=[
                Observation(
                    collection_id=str(task.metadata["collection_id"]),
                    analysis_id=request.analysis_id,
                    consumer=request.consumer,
                    source=self.source_id,
                    source_kind="test",
                    url=task.url,
                    entity_type="place",
                    text=task.goal,
                    content_hash=sha256_text(task.goal),
                )
            ]
        )

    async def normalize(self, result):
        return result

    async def health(self):
        return {"status": "ok"}


class WildcardSeedAdapter(CoveringAdapter):
    source_id = "wildcard_seed"
    intents = {"*"}

    async def discover(self, request):
        del request
        return [
            SourceTask(
                source_id=self.source_id,
                goal="seed",
                url="https://seed.example/page",
            )
        ]


class DiscoveredAdapter(CoveringAdapter):
    source_id = "discovered"
    intents = {"discovery_only"}

    async def discover(self, request):
        del request
        return []


class RecordingDiscovery:
    def __init__(self, *, max_queries: int = 8, create_tasks: bool = False) -> None:
        self.max_queries = max_queries
        self.create_tasks = create_tasks
        self.requests: list[tuple[list[str], list[str]]] = []
        self.created_goals: list[str] = []

    async def discover(self, queries, request):
        intents = list(request.intents)
        self.requests.append((list(queries), intents))
        tasks = []
        if self.create_tasks:
            goal = intents[0]
            self.created_goals.append(goal)
            tasks.append(
                SourceTask(
                    source_id="discovered",
                    goal=goal,
                    url=f"https://result.example/{goal}",
                    task_key=f"discovered:{goal}",
                )
            )
        return DiscoveryOutcome(tasks=tasks, providers_attempted=["fake"])


class EmptyProvider:
    name = "empty"

    async def discover(self, queries, request):
        del queries, request
        return []

    async def health(self):
        return {"provider": self.name, "status": "ok"}


class FakeGuard:
    async def validate(self, url):
        return url


async def run(tmp_path: Path, intents: list[str], discovery, extra_adapters=()):
    repo = SQLiteRepository(tmp_path / "db.sqlite")
    registry = SourceRegistry()
    registry.register(CoveringAdapter())
    for adapter in extra_adapters:
        registry.register(adapter)
    orchestrator = CollectionOrchestrator(
        repo,
        registry,
        HeuristicResearchPlanner(),
        discovery=discovery,
    )
    await orchestrator.start()
    accepted = await orchestrator.submit(
        CollectionRequest(
            consumer="test",
            analysis_id="1",
            territory={"city": "Ижевск"},
            intents=intents,
        )
    )
    await orchestrator._jobs[accepted.collection_id]
    record = await repo.get_collection(accepted.collection_id)
    await orchestrator.shutdown()
    return record


@pytest.mark.asyncio
async def test_fully_covered_intent_skips_discovery(tmp_path: Path):
    discovery = RecordingDiscovery()
    record = await run(tmp_path, ["school"], discovery)
    assert record is not None
    assert discovery.requests == []
    assert record.checkpoint["covered_intents"] == ["school"]


@pytest.mark.asyncio
async def test_mixed_request_discovers_only_uncovered_intent(tmp_path: Path):
    discovery = RecordingDiscovery()
    record = await run(tmp_path, ["school", "public_mentions"], discovery)
    assert record is not None
    assert len(discovery.requests) == 1
    _, discovered_intents = discovery.requests[0]
    assert discovered_intents == ["public_mentions"]


@pytest.mark.asyncio
async def test_mixed_request_with_empty_discovery_is_partial(tmp_path: Path):
    discovery = DiscoveryService([EmptyProvider()], FakeGuard())
    record = await run(tmp_path, ["school", "public_mentions"], discovery)
    assert record is not None
    assert record.status == CollectionStatus.PARTIAL
    assert record.partial is True
    assert any(error.code == "DISCOVERY_NO_RESULTS" for error in record.errors)


@pytest.mark.asyncio
async def test_wildcard_seed_does_not_precover_all_intents(tmp_path: Path):
    discovery = RecordingDiscovery()
    record = await run(
        tmp_path,
        ["public_mentions", "local_news"],
        discovery,
        extra_adapters=(WildcardSeedAdapter(),),
    )
    assert record is not None
    assert record.checkpoint["covered_intents"] == []
    assert [intents for _, intents in discovery.requests] == [
        ["public_mentions"],
        ["local_news"],
    ]


@pytest.mark.asyncio
async def test_multi_intent_discovery_tasks_keep_their_own_goal(tmp_path: Path):
    discovery = RecordingDiscovery(create_tasks=True)
    record = await run(
        tmp_path,
        ["public_mentions", "local_news"],
        discovery,
        extra_adapters=(DiscoveredAdapter(),),
    )
    assert record is not None
    assert discovery.created_goals == ["public_mentions", "local_news"]
    assert [intents for _, intents in discovery.requests] == [
        ["public_mentions"],
        ["local_news"],
    ]


@pytest.mark.asyncio
async def test_discovery_uses_one_global_query_budget_across_intents(tmp_path: Path):
    discovery = RecordingDiscovery(max_queries=2)
    record = await run(
        tmp_path,
        ["public_mentions", "local_news", "incidents"],
        discovery,
    )
    assert record is not None
    assert sum(len(queries) for queries, _ in discovery.requests) == 2
    assert [intents for _, intents in discovery.requests] == [
        ["public_mentions"],
        ["local_news"],
    ]
    assert any(
        error.code == "DISCOVERY_QUERY_BUDGET_EXHAUSTED" for error in record.errors
    )
