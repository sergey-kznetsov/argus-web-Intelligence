import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.contracts.models import CollectionRequest, CollectionStatus, Observation
from argus.history.snapshots import sha256_text
from argus.orchestrator.service import CollectionOrchestrator
from argus.research.discovery import DiscoveryOutcome
from argus.research.planner import HeuristicResearchPlanner
from argus.sources.base import SourceResult, SourceTask
from argus.sources.registry import SourceRegistry
from argus.storage.sqlite import SQLiteRepository


class DiscoveredAdapter:
    source_id = "discovered"
    intents = {"discovery_only"}

    async def discover(self, request):
        del request
        return []

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
                    entity_type="document",
                    text=task.goal,
                    content_hash=sha256_text(task.goal),
                )
            ]
        )

    async def normalize(self, result):
        return result

    async def health(self):
        return {"status": "ok"}


class InterruptSecondIntentDiscovery:
    max_queries = 8

    def __init__(self) -> None:
        self.requests: list[str] = []

    async def discover(self, queries, request):
        del queries
        intent = request.intents[0]
        self.requests.append(intent)
        if intent == "local_news":
            raise asyncio.CancelledError()
        return DiscoveryOutcome(
            tasks=[
                SourceTask(
                    source_id="discovered",
                    goal=intent,
                    url=f"https://result.example/{intent}",
                    task_key=f"discovered:{intent}",
                )
            ],
            providers_attempted=["fake"],
        )


class RecordingRecoveryDiscovery:
    max_queries = 8

    def __init__(self) -> None:
        self.requests: list[str] = []

    async def discover(self, queries, request):
        del queries
        intent = request.intents[0]
        self.requests.append(intent)
        return DiscoveryOutcome(
            tasks=[
                SourceTask(
                    source_id="discovered",
                    goal=intent,
                    url=f"https://result.example/{intent}",
                    task_key=f"discovered:{intent}",
                )
            ],
            providers_attempted=["fake"],
        )


def registry() -> SourceRegistry:
    value = SourceRegistry()
    value.register(DiscoveredAdapter())
    return value


@pytest.mark.asyncio
async def test_restart_resumes_only_unfinished_discovery_intent(tmp_path: Path):
    db_path = tmp_path / "argus.sqlite"
    first_repo = SQLiteRepository(db_path)
    first_discovery = InterruptSecondIntentDiscovery()
    first = CollectionOrchestrator(
        first_repo,
        registry(),
        HeuristicResearchPlanner(),
        discovery=first_discovery,
    )
    await first.start()
    accepted = await first.submit(
        CollectionRequest(
            consumer="test",
            analysis_id="recovery-1",
            territory={"city": "Ижевск"},
            intents=["public_mentions", "local_news"],
        )
    )

    with pytest.raises(asyncio.CancelledError):
        await first._jobs[accepted.collection_id]

    interrupted = await first_repo.get_collection(accepted.collection_id)
    assert interrupted is not None
    assert interrupted.status == CollectionStatus.RUNNING
    assert interrupted.checkpoint["planning_initial_tasks_complete"] is True
    assert interrupted.checkpoint["discovery_completed_intents"] == ["public_mentions"]
    assert first_discovery.requests == ["public_mentions", "local_news"]
    await first.shutdown()

    second_repo = SQLiteRepository(db_path)
    recovery_discovery = RecordingRecoveryDiscovery()
    recovered = CollectionOrchestrator(
        second_repo,
        registry(),
        HeuristicResearchPlanner(),
        discovery=recovery_discovery,
    )
    await recovered.start()
    await recovered._jobs[accepted.collection_id]

    record = await second_repo.get_collection(accepted.collection_id)
    observations = await second_repo.list_observations(accepted.collection_id)
    await recovered.shutdown()

    assert record is not None
    assert record.status == CollectionStatus.COMPLETED
    assert recovery_discovery.requests == ["local_news"]
    assert {item.text for item in observations} == {"public_mentions", "local_news"}
    assert record.checkpoint["discovery_completed_intents"] == [
        "local_news",
        "public_mentions",
    ]
