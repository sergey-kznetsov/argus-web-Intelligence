from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from argus.contracts.models import (
    CollectionConstraints,
    CollectionRecord,
    CollectionRequest,
    CollectionStatus,
    TerritoryContext,
    utcnow,
)
from argus.orchestrator.atomic import AtomicCollectionOrchestrator
from argus.sources.base import SourceTask
from argus.sources.registry import SourceRegistry


class UnusedPlanner:
    async def plan(self, request):
        raise AssertionError(f"planner is not used by this focused test: {request.analysis_id}")


class MemoryRecordRepository:
    def __init__(self, record: CollectionRecord) -> None:
        self.record = record.model_copy(deep=True)

    async def get_collection(self, collection_id: str):
        if collection_id != self.record.collection_id:
            return None
        return self.record.model_copy(deep=True)

    async def update_collection(self, record: CollectionRecord) -> None:
        self.record = record.model_copy(deep=True)

    async def list_observations(self, collection_id: str):
        assert collection_id == self.record.collection_id
        return []

    async def commit_task_success(self, *args, **kwargs):
        raise AssertionError("timed-out tasks must not commit factual data")


class BlockingAdapter:
    source_id = "blocking"
    intents = {"public_mentions"}

    async def discover(self, request):
        return []

    async def fetch(self, task):
        await asyncio.Event().wait()

    async def extract(self, task, fetched, request):
        raise AssertionError("fetch never completes")

    async def normalize(self, result):
        return result

    async def health(self):
        return {"status": "ok"}


def make_record(*, max_duration_seconds: float = 30.0) -> CollectionRecord:
    created = utcnow()
    return CollectionRecord(
        collection_id="collection-budget-test",
        request=CollectionRequest(
            consumer="budget-test",
            analysis_id="analysis-budget-test",
            territory=TerritoryContext(city="Пермь"),
            intents=["public_mentions"],
            constraints=CollectionConstraints(
                max_pages=3,
                max_duration_seconds=max_duration_seconds,
            ),
            allow_partial=True,
        ),
        status=CollectionStatus.RUNNING,
        created_at=created,
        updated_at=created,
        stage="collecting",
    )


def make_orchestrator(record: CollectionRecord) -> tuple[AtomicCollectionOrchestrator, MemoryRecordRepository]:
    repository = MemoryRecordRepository(record)
    registry = SourceRegistry()
    registry.register(BlockingAdapter())
    orchestrator = AtomicCollectionOrchestrator(
        repository=repository,
        registry=registry,
        planner=UnusedPlanner(),
        source_task_timeout_seconds=5.0,
    )
    return orchestrator, repository


@pytest.mark.asyncio
async def test_source_task_timeout_is_local_and_records_explicit_failure():
    record = make_record()
    orchestrator, repository = make_orchestrator(record)
    orchestrator.source_task_timeout_seconds = 0.05

    await orchestrator._process_tasks(
        record,
        [SourceTask(source_id="blocking", goal="public_mentions", url="https://example.com")],
    )

    terminal = repository.record
    assert terminal.status == CollectionStatus.FAILED
    assert any(error.code == "SOURCE_TASK_TIMEOUT" for error in terminal.errors)
    assert not any(error.code == "TIME_BUDGET_EXHAUSTED" for error in terminal.errors)
    assert terminal.coverage[-1].error_code == "SOURCE_TASK_TIMEOUT"
    assert terminal.checkpoint["execution_budget"]["processed_pages"] == 1
    assert terminal.checkpoint["execution_budget"]["time_budget_exhausted"] is False


@pytest.mark.asyncio
async def test_collection_time_budget_stops_before_next_task_and_preserves_pending_work():
    record = make_record(max_duration_seconds=30.0)
    record.checkpoint["execution_budget_started_at"] = (utcnow() - timedelta(seconds=31)).isoformat()
    orchestrator, repository = make_orchestrator(record)

    await orchestrator._process_tasks(
        record,
        [SourceTask(source_id="blocking", goal="public_mentions", url="https://example.com")],
    )

    terminal = repository.record
    assert terminal.status == CollectionStatus.FAILED
    assert any(error.code == "TIME_BUDGET_EXHAUSTED" for error in terminal.errors)
    assert not any(error.code == "PAGE_BUDGET_EXHAUSTED" for error in terminal.errors)
    assert terminal.checkpoint["execution_budget"]["time_budget_exhausted"] is True
    assert terminal.checkpoint["execution_budget"]["pending_tasks"] == 1
    pending = terminal.checkpoint["pending_tasks"]
    assert len(pending) == 1
    assert pending[0]["url"] == "https://example.com"
