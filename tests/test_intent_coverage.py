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
                    text="school",
                    content_hash=sha256_text("school"),
                )
            ]
        )

    async def normalize(self, result):
        return result

    async def health(self):
        return {"status": "ok"}


class RecordingDiscovery:
    def __init__(self) -> None:
        self.requests = []

    async def discover(self, queries, request):
        self.requests.append((queries, list(request.intents)))
        return DiscoveryOutcome(providers_attempted=["fake"])


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


async def run(tmp_path: Path, intents: list[str], discovery):
    repo = SQLiteRepository(tmp_path / "db.sqlite")
    registry = SourceRegistry()
    registry.register(CoveringAdapter())
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
