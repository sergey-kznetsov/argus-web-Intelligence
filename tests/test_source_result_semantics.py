from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.contracts.models import (
    CollectionRequest,
    CollectionStatus,
    Observation,
    StructuredError,
)
from argus.history.snapshots import sha256_text
from argus.orchestrator.service import CollectionOrchestrator
from argus.research.planner import HeuristicResearchPlanner
from argus.sources.base import SourceResult, SourceTask
from argus.sources.registry import SourceRegistry
from argus.storage.sqlite import SQLiteRepository


class ResultAdapter:
    source_id = "result-test"
    intents = {"test"}

    def __init__(self, result_factory) -> None:
        self.result_factory = result_factory

    async def discover(self, request):
        del request
        return [SourceTask(source_id=self.source_id, goal="test", url="https://example.com/")]

    async def fetch(self, task):
        return SimpleNamespace(url=task.url)

    async def extract(self, task, fetched, request):
        del fetched
        return self.result_factory(task, request)

    async def normalize(self, result):
        return result

    async def health(self):
        return {"status": "ok"}


def request():
    return CollectionRequest(
        consumer="test",
        analysis_id="1",
        territory={"city": "Ижевск"},
        intents=["test"],
    )


async def run(tmp_path: Path, adapter: ResultAdapter):
    repo = SQLiteRepository(tmp_path / "db.sqlite")
    registry = SourceRegistry()
    registry.register(adapter)
    orchestrator = CollectionOrchestrator(repo, registry, HeuristicResearchPlanner())
    await orchestrator.start()
    accepted = await orchestrator.submit(request())
    await orchestrator._jobs[accepted.collection_id]
    record = await repo.get_collection(accepted.collection_id)
    await orchestrator.shutdown()
    return record


@pytest.mark.asyncio
async def test_blocking_source_error_keeps_collection_blocked(tmp_path: Path):
    def result_factory(task, request_value):
        del task, request_value
        return SourceResult(
            observations=[],
            blocked=True,
            errors=[
                StructuredError(
                    code="MAP_PROVIDER_BLOCKED",
                    message="rate limited",
                    retryable=True,
                    source_id="map:test",
                )
            ],
        )

    record = await run(tmp_path, ResultAdapter(result_factory))
    assert record and record.status == CollectionStatus.BLOCKED
    assert record.coverage[0].status == "blocked"
    assert record.coverage[0].error_code == "MAP_PROVIDER_BLOCKED"


@pytest.mark.asyncio
async def test_partial_source_with_data_marks_collection_partial(tmp_path: Path):
    def result_factory(task, request_value):
        observation = Observation(
            collection_id=str(task.metadata["collection_id"]),
            analysis_id=request_value.analysis_id,
            consumer=request_value.consumer,
            source="result-test",
            source_kind="test",
            url=task.url,
            entity_type="document",
            text="fact",
            content_hash=sha256_text("fact"),
        )
        return SourceResult(
            observations=[observation],
            partial=True,
            errors=[
                StructuredError(
                    code="SOURCE_PAGE_TRUNCATED",
                    message="some results were unavailable",
                    retryable=True,
                    source_id="result-test",
                )
            ],
        )

    record = await run(tmp_path, ResultAdapter(result_factory))
    assert record and record.status == CollectionStatus.PARTIAL
    assert record.coverage[0].status == "partial"
    assert record.errors[0].code == "SOURCE_PAGE_TRUNCATED"
