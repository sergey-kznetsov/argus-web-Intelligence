from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.contracts.models import CollectionRequest, CollectionStatus, Observation
from argus.history.snapshots import sha256_text
from argus.orchestrator.service import CollectionOrchestrator
from argus.research.planner import ResearchPlan
from argus.sources.base import SourceResult, SourceTask
from argus.sources.registry import SourceRegistry
from argus.storage.sqlite import SQLiteRepository


INTENT = "residential_premises_count"


class DirectOnlyPlanner:
    async def plan(self, request: CollectionRequest) -> ResearchPlan:
        assert request.intents == [INTENT]
        return ResearchPlan(
            queries=[],
            tasks=[
                SourceTask(
                    source_id="mingkh_residential",
                    goal=INTENT,
                    url="https://dom.mingkh.ru/",
                    task_key=f"mingkh_residential:test:{request.analysis_id}",
                    metadata={
                        "research_goals": [INTENT],
                        "external_discovery_allowed": False,
                        "dedicated_source_direct_entry": True,
                        "source_owned_navigation": True,
                    },
                )
            ],
            notes=["direct-only test plan"],
        )


class DirectAdapter:
    source_id = "mingkh_residential"
    intents = {INTENT}

    async def discover(self, request: CollectionRequest) -> list[SourceTask]:
        # This reproduces the real Mingkh adapter: without caller seed URLs the adapter
        # does not create an initial task; the planner owns the direct source entry.
        assert not request.constraints.seed_urls
        return []

    async def fetch(self, task: SourceTask):
        return SimpleNamespace(final_url=task.url)

    async def extract(self, task, fetched, request: CollectionRequest) -> SourceResult:
        del fetched
        text = "Количество квартир: 32"
        return SourceResult(
            observations=[
                Observation(
                    collection_id=str(task.metadata["collection_id"]),
                    analysis_id=request.analysis_id,
                    consumer=request.consumer,
                    source=self.source_id,
                    source_kind="public_web",
                    url="https://dom.mingkh.ru/test-house",
                    entity_type="residential_building_fact",
                    text=text,
                    content_hash=sha256_text(text),
                )
            ]
        )

    async def normalize(self, result: SourceResult) -> SourceResult:
        return result

    async def health(self):
        return {"status": "ok"}


class MustNotRunExternalDiscovery:
    max_queries = 8

    def __init__(self) -> None:
        self.calls = 0

    async def discover(self, queries, request):
        self.calls += 1
        raise AssertionError(
            f"external discovery must not run for direct no-discovery task: {queries!r} {request.intents!r}"
        )


@pytest.mark.asyncio
async def test_direct_no_discovery_planner_task_prevents_discovery_no_queries(tmp_path: Path):
    repository = SQLiteRepository(tmp_path / "argus.sqlite")
    registry = SourceRegistry()
    registry.register(DirectAdapter())
    discovery = MustNotRunExternalDiscovery()
    orchestrator = CollectionOrchestrator(
        repository,
        registry,
        DirectOnlyPlanner(),
        discovery=discovery,
    )
    await orchestrator.start()
    try:
        request = CollectionRequest(
            consumer="janus.parking.potential.uds",
            analysis_id="janus-direct-orchestration",
            territory={
                "city": "Ижевск",
                "address": "Ижевск, Пушкинская улица, 277",
            },
            intents=[INTENT],
            constraints={"max_pages": 1, "max_depth": 0},
            allow_partial=True,
        )
        accepted = await orchestrator.submit(request)
        await orchestrator._jobs[accepted.collection_id]
        record = await repository.get_collection(accepted.collection_id)

        assert record is not None
        assert record.status == CollectionStatus.COMPLETED
        assert discovery.calls == 0
        assert record.checkpoint["discovery_queries"] == []
        assert INTENT in record.checkpoint["discovery_completed_intents"]
        assert not any(error.code == "DISCOVERY_NO_QUERIES" for error in record.errors)

        observations = await repository.list_observations(accepted.collection_id)
        assert len(observations) == 1
        assert observations[0].source == "mingkh_residential"
        assert observations[0].url == "https://dom.mingkh.ru/test-house"
    finally:
        await orchestrator.shutdown()
