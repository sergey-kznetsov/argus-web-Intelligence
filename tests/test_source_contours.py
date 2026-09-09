from __future__ import annotations

from types import SimpleNamespace

import pytest

from argus.contracts.models import CollectionRequest, Observation
from argus.orchestrator.service import CollectionOrchestrator
from argus.orchestrator.toolpack_aware import (
    ToolPackAwareEvidenceStatusAdaptiveResearchOrchestrator,
)
from argus.research.discovery import DiscoveryOutcome
from argus.research.source_contours import SourceContourResearchPlanner
from argus.sources.base import SourceTask
from argus.sources.intent_evidence_web import IntentEvidenceWebAdapter
from argus.toolpacks import TOOL_PACK_REGISTRY, activate_tool_pack


EXPECTED_CONTOURS = {
    "official_government",
    "public_appeals",
    "housing_utilities",
    "local_forums",
    "local_media",
    "public_communities",
    "general_web",
}


def kraken_request(*, radius: int | None = 1000) -> CollectionRequest:
    territory = {
        "city": "Ижевск",
        "address": "Пушкинская улица, 277",
        "point": {"latitude": 56.8527, "longitude": 53.2115},
        "metadata": {
            "region": "Удмуртская Республика",
            "street": "Пушкинская улица",
            "house": "277",
        },
    }
    if radius is not None:
        territory["radius_meters"] = radius
    return CollectionRequest(
        consumer="kraken.development.uds",
        consumer_profile_version=1,
        capability="urban_signals",
        analysis_id="source-contours",
        territory=territory,
        intents=[
            "comments",
            "discussions",
            "complaints",
            "incidents",
            "posts",
            "public_appeals",
            "resident_messages",
            "local_news",
        ],
        constraints={"language": "ru", "max_pages": 30},
    )


def test_urban_signal_contours_cover_independent_public_source_classes() -> None:
    planner = SourceContourResearchPlanner()
    plans = planner.plans(kraken_request(), planner_policy="urban_signals")

    assert {plan.contour_id for plan in plans} == EXPECTED_CONTOURS
    all_queries = [query for plan in plans for query in plan.queries]

    assert all("277" not in query for query in all_queries)
    assert all("Ижевск" in query for query in all_queries)
    assert any("Удмуртская Республика" in query for query in all_queries)
    assert any("администрация" in query for query in all_queries)
    assert any("обращения граждан" in query for query in all_queries)
    assert any("dom.gosuslugi.ru" in query for query in all_queries)
    assert any("dom.mingkh.ru" in query for query in all_queries)
    assert any("форум" in query for query in all_queries)
    assert any("местные СМИ" in query for query in all_queries)
    assert any("сообщество" in query for query in all_queries)

    protected = {
        plan.contour_id: plan
        for plan in plans
        if plan.contour_id in {"official_government", "public_appeals", "housing_utilities"}
    }
    assert protected
    assert all("rubrikator.org" in plan.denied_domain_roots for plan in protected.values())
    assert all("yandex.ru" in plan.denied_domain_roots for plan in protected.values())
    assert all("2gis.ru" in plan.denied_domain_roots for plan in protected.values())
    assert all("google.com" in plan.denied_domain_roots for plan in protected.values())


def test_radius_contours_drop_unverified_poi_pseudo_street() -> None:
    request = CollectionRequest(
        consumer="kraken.development.uds",
        consumer_profile_version=1,
        capability="urban_signals",
        analysis_id="poi-source-contours",
        territory={
            "city": "Ижевск",
            "address": "Ижевск, Parus Plaza, бизнес-центр",
            "point": {"latitude": 56.866315, "longitude": 53.207313},
            "radius_meters": 1200,
            "metadata": {"street": "Parus Plaza бизнес-центр"},
        },
        intents=["complaints", "local_news"],
        constraints={"language": "ru", "max_pages": 30},
    )

    plans = SourceContourResearchPlanner().plans(
        request,
        planner_policy="urban_signals",
    )
    all_queries = [query for plan in plans for query in plan.queries]

    assert plans
    assert all("Parus Plaza" not in query for query in all_queries)
    assert all("бизнес-центр" not in query for query in all_queries)
    assert all("Ижевск" in query for query in all_queries)


def test_radius_contours_expand_across_named_overpass_streets() -> None:
    request = CollectionRequest(
        consumer="kraken.development.uds",
        consumer_profile_version=1,
        capability="urban_signals",
        analysis_id="radius-street-contours",
        territory={
            "city": "Ижевск",
            "address": "Ижевск, Parus Plaza, бизнес-центр",
            "point": {"latitude": 56.866315, "longitude": 53.207313},
            "radius_meters": 1200,
            "metadata": {"street": "Parus Plaza бизнес-центр"},
        },
        intents=["complaints", "local_news"],
        constraints={"language": "ru", "max_pages": 30},
    )
    streets = [
        Observation(
            observation_id=f"street-{index}",
            collection_id="c1",
            analysis_id=request.analysis_id,
            consumer=request.consumer,
            source="openstreetmap_overpass",
            source_kind="map_place",
            url=f"https://www.openstreetmap.org/way/{index}",
            entity_type="place",
            title=name,
            data={"name": name, "categories": ["highway:residential"]},
            geo={"latitude": latitude, "longitude": 53.207313},
            content_hash=f"hash-{index}",
        )
        for index, (name, latitude) in enumerate(
            [("Пушкинская улица", 56.8664), ("улица Лихвинцева", 56.8670)],
            start=1,
        )
    ]

    plans = SourceContourResearchPlanner().plans(
        request,
        planner_policy="urban_signals",
        observations=streets,
    )

    assert plans
    for plan in plans:
        assert any("Пушкинская улица" in query for query in plan.queries)
        assert any("улица Лихвинцева" in query for query in plan.queries)
        assert all("Parus Plaza" not in query for query in plan.queries)


def test_contours_keep_exact_address_when_no_radius_was_requested() -> None:
    planner = SourceContourResearchPlanner()
    plans = planner.plans(kraken_request(radius=None), planner_policy="urban_signals")

    assert plans
    assert any("277" in query for plan in plans for query in plan.queries)


def test_unrelated_planner_policy_does_not_receive_urban_signal_contours() -> None:
    planner = SourceContourResearchPlanner()

    assert planner.plans(kraken_request(), planner_policy="generic_research") == []


def test_source_contour_provenance_preserves_plain_web_page_source_shape() -> None:
    task = SourceTask(
        source_id="generic_web",
        goal="complaints",
        url="https://official.example/appeal",
        metadata={
            "source_contour": "official_government",
            "source_contour_version": "source-contours/4",
            "source_contour_priority": 10,
        },
    )
    observation = SimpleNamespace(
        entity_type="document",
        source_kind="web_page",
        provenance={},
        quality={},
    )
    evidence = SimpleNamespace(metadata={})
    result = SimpleNamespace(observations=[observation], evidence=[evidence])

    IntentEvidenceWebAdapter._attach_source_contour_provenance(task, result)

    assert observation.source_kind == "web_page"
    assert observation.quality["source_contour_traced"] is True
    assert observation.provenance["source_contour"]["navigation_only"] is True
    assert observation.provenance["source_contour"]["contour_label_is_evidence"] is False
    assert evidence.metadata["source_contour"]["contour_id"] == "official_government"


class FakeRepository:
    def __init__(self) -> None:
        self.updates = 0

    async def update_collection(self, record) -> None:
        del record
        self.updates += 1


class FakeDiscovery:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], int, tuple[str, ...]]] = []

    async def discover(self, queries, request) -> DiscoveryOutcome:
        self.calls.append(
            (
                list(queries),
                request.constraints.max_pages,
                tuple(request.constraints.denied_domains),
            )
        )
        index = len(self.calls)
        task = SourceTask(
            source_id="generic_web",
            goal=request.intents[0],
            url=f"https://source-{index}.example/path",
            depth=0,
            metadata={"research_goals": list(request.intents)},
        )
        return DiscoveryOutcome(
            tasks=[task],
            providers_attempted=["fake_search"],
            candidates_seen=3,
            valid_destinations=1,
            destinations_selected=1,
            task_budget=request.constraints.max_pages,
            stop_reason="first_provider_with_valid_destinations",
        )


class ContourHarness:
    def __init__(self) -> None:
        self.discovery = FakeDiscovery()
        self.repository = FakeRepository()
        self.source_contour_planner = SourceContourResearchPlanner()

    def _merge_tasks(self, existing, additions, collection_id):
        return CollectionOrchestrator._merge_tasks(existing, additions, collection_id)

    def _task_dict(self, task):
        return CollectionOrchestrator._task_dict(task)


@pytest.mark.asyncio
async def test_tool_pack_orchestrator_executes_each_contour_as_an_independent_lane() -> None:
    harness = ContourHarness()
    request = kraken_request()
    record = SimpleNamespace(
        collection_id="collection-contours",
        request=request,
        checkpoint={},
        stage="planning",
        updated_at=None,
    )
    pack = TOOL_PACK_REGISTRY.resolve(
        consumer_id="kraken.development.uds",
        capability="urban_signals",
        expected_tool_pack_id="kraken.urban_signals",
        requested_tool_pack_id=request.tool_pack_id,
        requested_version=request.tool_pack_version,
    )

    with activate_tool_pack(pack):
        pending = await (
            ToolPackAwareEvidenceStatusAdaptiveResearchOrchestrator._discover_source_contours(
                harness,
                record,
                [],
            )
        )

    assert len(harness.discovery.calls) == len(EXPECTED_CONTOURS)
    assert {task.metadata["source_contour"] for task in pending} == EXPECTED_CONTOURS
    assert record.checkpoint["source_contours_complete"] is True
    assert set(record.checkpoint["source_contours"]) == EXPECTED_CONTOURS
    assert all(
        state["status"] == "discovered"
        for state in record.checkpoint["source_contours"].values()
    )
    assert all(
        task.metadata["source_contour_version"] == "source-contours/5"
        for task in pending
    )
    protected_calls = harness.discovery.calls[:3]
    assert all("rubrikator.org" in denied for _, _, denied in protected_calls)
    assert all("yandex.ru" in denied for _, _, denied in protected_calls)


def test_radius_inventory_then_contours_then_direct_maps_outrank_map_fanout() -> None:
    requested = {"complaints", "comments"}
    inventory = SourceTask(
        source_id="openstreetmap_overpass",
        goal="area_street_inventory",
        url="https://overpass-api.de/api/interpreter",
        depth=0,
    )
    official = SourceTask(
        source_id="generic_web",
        goal="complaints",
        url="https://official.example/appeal",
        depth=0,
        metadata={
            "source_contour": "official_government",
            "source_contour_priority": 10,
        },
    )
    direct_map = SourceTask(
        source_id="generic_web",
        goal="complaints",
        url="https://2gis.ru/search/example",
        depth=0,
        metadata={"public_map_direct_navigation": True},
    )
    map_fanout = SourceTask(
        source_id="generic_web",
        goal="complaints",
        url="https://yandex.ru/maps/example",
        depth=0,
        metadata={"curated_public_map_round": 1},
    )

    priority = ToolPackAwareEvidenceStatusAdaptiveResearchOrchestrator._pending_priority

    assert priority(inventory, requested) < priority(official, requested)
    assert priority(official, requested) < priority(direct_map, requested)
    assert priority(direct_map, requested) < priority(map_fanout, requested)
    assert (
        ToolPackAwareEvidenceStatusAdaptiveResearchOrchestrator._focused_branch(official)
        == "source_contour"
    )
    assert (
        ToolPackAwareEvidenceStatusAdaptiveResearchOrchestrator._focused_branch(direct_map)
        == "public_map_direct"
    )
