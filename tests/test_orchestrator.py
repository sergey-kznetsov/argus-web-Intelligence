from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.contracts.models import CollectionRequest, CollectionStatus, Observation
from argus.history.snapshots import sha256_text
from argus.orchestrator.service import CollectionOrchestrator
from argus.research.planner import HeuristicResearchPlanner
from argus.sources.base import SourceResult, SourceTask
from argus.sources.registry import SourceRegistry
from argus.storage.sqlite import SQLiteRepository


class FakeAdapter:
    source_id = "fake"
    intents = {"reviews"}

    def __init__(self, *, blocked: bool = False, children: int = 0) -> None:
        self.blocked = blocked
        self.children = children

    async def discover(self, request):
        del request
        return [
            SourceTask(
                source_id=self.source_id,
                goal="reviews",
                url="https://example.com/root",
            )
        ]

    async def fetch(self, task):
        return SimpleNamespace(final_url=task.url)

    async def extract(self, task, fetched, request):
        del fetched
        if self.blocked:
            return SourceResult(observations=[], blocked=True)
        observation = Observation(
            collection_id=str(task.metadata["collection_id"]),
            analysis_id=request.analysis_id,
            consumer=request.consumer,
            source=self.source_id,
            source_kind="test",
            url=task.url,
            entity_type="document",
            text="evidence",
            content_hash=sha256_text("evidence"),
        )
        children = []
        if task.depth < self.children:
            children.append(
                SourceTask(
                    source_id=self.source_id,
                    goal=task.goal,
                    url=f"https://example.com/{task.depth + 1}",
                    depth=task.depth + 1,
                )
            )
        return SourceResult(observations=[observation], discovered_tasks=children)

    async def normalize(self, result):
        return result

    async def health(self):
        return {"status": "ok"}


async def run_collection(tmp_path: Path, adapter, **request_overrides):
    repo = SQLiteRepository(tmp_path / "db.sqlite")
    registry = SourceRegistry()
    if adapter:
        registry.register(adapter)
    orchestrator = CollectionOrchestrator(repo, registry, HeuristicResearchPlanner())
    await orchestrator.start()
    payload = {
        "consumer": "test",
        "analysis_id": "1",
        "territory": {"city": "Ижевск"},
        "intents": ["reviews"],
    }
    payload.update(request_overrides)
    accepted = await orchestrator.submit(CollectionRequest(**payload))
    await orchestrator._jobs[accepted.collection_id]
    record = await repo.get_collection(accepted.collection_id)
    await orchestrator.shutdown()
    return repo, record


@pytest.mark.asyncio
async def test_collection_without_executable_sources_fails_explicitly(tmp_path: Path):
    _, record = await run_collection(tmp_path, None)
    assert record and record.status == CollectionStatus.FAILED
    assert record.errors[0].code == "NO_SOURCE_TASKS"


@pytest.mark.asyncio
async def test_fully_blocked_collection_is_blocked(tmp_path: Path):
    _, record = await run_collection(tmp_path, FakeAdapter(blocked=True))
    assert record and record.status == CollectionStatus.BLOCKED
    assert record.coverage[0].blocked is True


@pytest.mark.asyncio
async def test_page_budget_marks_result_partial(tmp_path: Path):
    _, record = await run_collection(
        tmp_path,
        FakeAdapter(children=3),
        constraints={"max_pages": 1},
    )
    assert record and record.status == CollectionStatus.PARTIAL
    assert any(error.code == "PAGE_BUDGET_EXHAUSTED" for error in record.errors)
    assert record.checkpoint["pending_tasks"]


@pytest.mark.asyncio
async def test_allow_partial_false_fails_on_truncated_collection(tmp_path: Path):
    _, record = await run_collection(
        tmp_path,
        FakeAdapter(children=3),
        constraints={"max_pages": 1},
        allow_partial=False,
    )
    assert record and record.status == CollectionStatus.FAILED
    assert record.partial is False
