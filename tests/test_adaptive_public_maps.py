from __future__ import annotations

from types import SimpleNamespace

import pytest

from argus.contracts.models import CollectionRequest, Observation
from argus.orchestrator.adaptive_atomic import AdaptiveResearchAtomicCollectionOrchestrator
from argus.research.discovery import DiscoveryOutcome
from argus.sources.base import SourceTask


class RepositoryStub:
    def __init__(self, observations: list[Observation]) -> None:
        self.observations = observations

    async def list_observations(self, collection_id: str) -> list[Observation]:
        del collection_id
        return list(self.observations)


class DiscoveryStub:
    max_queries = 8

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], CollectionRequest]] = []

    async def discover(
        self,
        queries: list[str],
        request: CollectionRequest,
    ) -> DiscoveryOutcome:
        self.calls.append((list(queries), request))
        return DiscoveryOutcome(
            tasks=[
                SourceTask(
                    source_id="generic_web",
                    goal="reviews",
                    url="https://2gis.ru/izhevsk/firm/example",
                    metadata={},
                )
            ]
        )


class OrchestratorForTest(AdaptiveResearchAtomicCollectionOrchestrator):
    @staticmethod
    def _merge_tasks(
        pending: list[SourceTask],
        additions: list[SourceTask],
        collection_id: str,
    ) -> None:
        existing = {task.dedupe_key for task in pending}
        for task in additions:
            task.metadata["collection_id"] = collection_id
            if task.dedupe_key in existing:
                continue
            existing.add(task.dedupe_key)
            pending.append(task)


def request(*intents: str) -> CollectionRequest:
    return CollectionRequest(
        consumer="adaptive-map-test",
        analysis_id="adaptive-map-analysis",
        territory={"city": "Ижевск", "address": "Пушкинская, 277"},
        intents=list(intents),
        constraints={"max_pages": 12},
    )


def organization() -> Observation:
    return Observation(
        observation_id="org-1",
        collection_id="collection-map-1",
        analysis_id="adaptive-map-analysis",
        consumer="adaptive-map-test",
        source="generic_web",
        source_kind="structured_entity",
        url="https://example.test/cafe",
        entity_type="organization",
        entity_id="cafe-1",
        title="Кофейня Север",
        data={"name": "Кофейня Север"},
        provenance={},
        quality={},
    )


def build_orchestrator(repository: RepositoryStub, discovery: DiscoveryStub):
    return OrchestratorForTest(
        repository=repository,
        registry=SimpleNamespace(),
        planner=SimpleNamespace(),
        discovery=discovery,
        max_concurrency=1,
        auto_execute=False,
        max_followup_rounds=0,
        max_curated_historical_rounds=0,
        max_curated_public_map_rounds=2,
    )


@pytest.mark.asyncio
async def test_discovered_entity_gets_bounded_public_map_followup_queries():
    observation = organization()
    repository = RepositoryStub([observation])
    discovery = DiscoveryStub()
    orchestrator = build_orchestrator(repository, discovery)
    territory = "Ижевск, Пушкинская, 277"
    initial_map_queries = [
        f'site:yandex.ru/maps "{territory}" отзывы',
        f'site:2gis.ru "{territory}" отзывы',
        f'site:google.com/maps "{territory}" отзывы',
    ]
    record = SimpleNamespace(
        collection_id="collection-map-1",
        request=request("reviews", "local_news"),
        checkpoint={"queries": initial_map_queries},
        errors=[],
    )
    pending: list[SourceTask] = []
    current_task = SourceTask(
        source_id="generic_web",
        goal="reviews",
        url="https://example.test/cafe",
        metadata={},
    )

    await orchestrator._expand_curated_public_map_sources(
        record,
        current_task,
        [observation],
        pending,
        set(),
    )

    assert len(discovery.calls) == 1
    queries, branch_request = discovery.calls[0]
    assert len(queries) == 3
    assert all("Кофейня Север" in query for query in queries)
    assert queries[0].startswith('site:yandex.ru/maps "Кофейня Север"')
    assert queries[1].startswith('site:2gis.ru "Кофейня Север"')
    assert queries[2].startswith('site:google.com/maps "Кофейня Север"')
    assert branch_request.intents == ["reviews"]
    assert len(pending) == 1
    assert pending[0].metadata["curated_public_map_round"] == 1
    assert pending[0].metadata["collection_id"] == "collection-map-1"
    assert record.checkpoint["curated_public_map_rounds"] == 1
    assert record.checkpoint["curated_public_map_last_candidates"] == 1
    assert record.checkpoint["curated_public_map_source_version"] == "public-map-sources/1"


@pytest.mark.asyncio
async def test_public_map_followup_does_not_run_for_unrelated_intents():
    observation = organization()
    repository = RepositoryStub([observation])
    discovery = DiscoveryStub()
    orchestrator = build_orchestrator(repository, discovery)
    record = SimpleNamespace(
        collection_id="collection-map-2",
        request=request("local_news"),
        checkpoint={},
        errors=[],
    )

    await orchestrator._expand_curated_public_map_sources(
        record,
        SourceTask(
            source_id="generic_web",
            goal="local_news",
            url="https://example.test/news",
            metadata={},
        ),
        [observation],
        [],
        set(),
    )

    assert discovery.calls == []
    assert record.checkpoint == {}
