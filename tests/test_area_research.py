from __future__ import annotations

from argus.contracts.models import (
    CollectionConstraints,
    CollectionRequest,
    Observation,
    Point,
    TerritoryContext,
)
from argus.research.entities import AreaEntityResearchPlanner
from argus.research.followup import HeuristicFollowupResearchPlanner
from argus.sources.overpass_map import OverpassSourceAdapter


class _Provider:
    provider_id = "openstreetmap_overpass"
    endpoint = "https://overpass.example/api/interpreter"


def _request(*intents: str) -> CollectionRequest:
    return CollectionRequest(
        consumer="test",
        analysis_id="area-research",
        territory=TerritoryContext(
            city="Ижевск",
            address="Пушкинская, 277",
            point=Point(latitude=56.8527, longitude=53.2115),
            radius_meters=1000,
        ),
        intents=list(intents),
        constraints=CollectionConstraints(max_pages=100, max_depth=3, language="ru"),
    )


def _map_observation(name: str) -> Observation:
    return Observation(
        collection_id="collection",
        analysis_id="analysis",
        consumer="test",
        source="openstreetmap_overpass",
        source_kind="map_place",
        url="https://www.openstreetmap.org/node/1",
        entity_type="place",
        entity_id=f"osm:{name}",
        title=name,
        text="Ижевск",
        data={"name": name, "address": "Ижевск, Пушкинская"},
        content_hash="a" * 64,
    )


def test_area_entity_planner_expands_nearby_places_into_public_research_queries():
    request = _request("reviews", "comments", "complaints", "historical_context")
    planner = AreaEntityResearchPlanner(max_entities_per_expansion=4, max_queries_per_entity=4)

    queries = planner.expand(
        request,
        [_map_observation("Школа № 1"), _map_observation("Кафе Север")],
        seen_queries=set(),
        limit=8,
    )

    assert queries
    joined = "\n".join(queries)
    assert "Школа № 1" in joined
    assert "Кафе Север" in joined
    assert "отзывы" in joined
    assert "комментарии" in joined
    assert "жалобы" in joined
    assert "история" in joined
    assert all("Пушкинская" in query for query in queries)


def test_area_entity_planner_deduplicates_seen_queries():
    request = _request("reviews")
    planner = AreaEntityResearchPlanner(max_queries_per_entity=1)
    first = planner.expand(request, [_map_observation("Кафе Север")], seen_queries=set(), limit=2)
    second = planner.expand(
        request,
        [_map_observation("Кафе Север")],
        seen_queries=set(first),
        limit=2,
    )

    assert first
    assert second == []


async def test_overpass_area_inventory_runs_for_research_intents_without_hiding_web_discovery():
    adapter = OverpassSourceAdapter(_Provider(), snapshots=None, geocoder=None)  # type: ignore[arg-type]
    tasks = await adapter.discover(_request("reviews", "comments", "historical_context"))

    inventory = [task for task in tasks if task.goal == "area_entity_inventory"]
    assert len(inventory) == 1
    raw = inventory[0].metadata["map_request"]
    assert isinstance(raw, dict)
    assert raw["limit"] == 100
    assert raw["radius_meters"] == 1000
    assert len(raw["categories"]) > 5
    assert "*" in adapter.intents


async def test_followup_planner_requests_missing_intents_and_stops_when_covered():
    request = _request("reviews", "complaints", "historical_context")
    planner = HeuristicFollowupResearchPlanner(target_hits_per_intent=1)

    missing = await planner.plan_followups(
        request,
        [],
        seen_queries=set(),
        max_queries=5,
    )
    assert any("отзывы" in query for query in missing.queries)
    assert any("жалобы" in query for query in missing.queries)
    assert any("архив" in query or "история" in query for query in missing.queries)

    covered_observations = [
        Observation(
            collection_id="collection",
            analysis_id="analysis",
            consumer="test",
            source="generic_web",
            source_kind="web_page",
            url="https://example.org/reviews",
            entity_type="document",
            title="Отзывы",
            data={"research_goals": ["reviews", "complaints"]},
            content_hash="b" * 64,
        ),
        Observation(
            collection_id="collection",
            analysis_id="analysis",
            consumer="test",
            source="generic_web",
            source_kind="historical_page_version",
            url="https://web.archive.org/example",
            entity_type="historical_page_version",
            title="История",
            data={"research_goals": ["historical_context"]},
            content_hash="c" * 64,
        ),
    ]
    covered = await planner.plan_followups(
        request,
        covered_observations,
        seen_queries=set(),
        max_queries=5,
    )
    assert covered.queries == []
