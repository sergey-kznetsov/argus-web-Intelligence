from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from argus.bootstrap import SERVER_DEFAULT_OVERPASS_URL, effective_map_settings
from argus.config import Settings
from argus.contracts.models import CollectionRequest, Point, TerritoryContext
from argus.research.followup import FollowupPlan
from argus.research.planner import ResearchPlan
from argus.research.radius_scope import (
    RadiusAwareFollowupResearchPlanner,
    RadiusAwareResearchPlanner,
    exact_territory_text,
    radius_scope_text,
)
from argus.sources.overpass_map import OverpassSourceAdapter


def request(*, radius: int | None = 1000) -> CollectionRequest:
    return CollectionRequest(
        consumer="test",
        analysis_id="radius-scope",
        territory=TerritoryContext(
            city="Ижевск",
            address="Пушкинская улица, 277",
            point=Point(latitude=56.8527, longitude=53.2115),
            radius_meters=radius,
            metadata={"street": "Пушкинская улица", "house": "277"},
        ),
        intents=["complaints", "discussions"],
        constraints={"language": "ru", "max_pages": 30},
    )


def test_radius_scope_broadens_house_to_area_but_preserves_exact_helper() -> None:
    value = request()

    assert exact_territory_text(value) == "Ижевск, Пушкинская улица, 277"
    assert radius_scope_text(value) == "Ижевск, Пушкинская улица"
    assert radius_scope_text(value, entity_scope=True) == "Ижевск"


def test_without_radius_scope_remains_exact_address() -> None:
    value = request(radius=None)

    assert radius_scope_text(value) == "Ижевск, Пушкинская улица, 277"
    assert radius_scope_text(value, entity_scope=True) == "Ижевск, Пушкинская улица, 277"


@dataclass
class InitialDelegate:
    seen_addresses: list[str | None] = field(default_factory=list)

    async def plan(self, value: CollectionRequest) -> ResearchPlan:
        self.seen_addresses.append(value.territory.address)
        return ResearchPlan(queries=[f"{value.territory.address} жалобы"])


@dataclass
class FollowupDelegate:
    seen_addresses: list[str | None] = field(default_factory=list)

    async def plan_followups(
        self,
        value: CollectionRequest,
        observations,
        *,
        seen_queries: set[str],
        max_queries: int,
    ) -> FollowupPlan:
        del observations, seen_queries, max_queries
        self.seen_addresses.append(value.territory.address)
        return FollowupPlan(queries=[f"{value.territory.address} обсуждение"])


@pytest.mark.asyncio
async def test_initial_and_followup_planners_receive_area_scope() -> None:
    initial = InitialDelegate()
    followup = FollowupDelegate()
    value = request()

    initial_plan = await RadiusAwareResearchPlanner(initial).plan(value)
    followup_plan = await RadiusAwareFollowupResearchPlanner(followup).plan_followups(
        value,
        [],
        seen_queries=set(),
        max_queries=3,
    )

    assert initial.seen_addresses == ["Ижевск, Пушкинская улица"]
    assert followup.seen_addresses == ["Ижевск, Пушкинская улица"]
    assert "277" not in initial_plan.queries[0]
    assert "277" not in followup_plan.queries[0]
    assert any("radius_meters=1000" in note for note in initial_plan.notes)
    assert any("radius_meters=1000" in note for note in followup_plan.notes)


def test_standalone_server_enables_free_overpass_inventory_by_default() -> None:
    server = Settings(
        execution_role="worker",
        storage_backend="postgresql",
        database_dsn="postgresql://argus:secret@127.0.0.1:5432/argus",
    )
    embedded = Settings()
    explicit = Settings(
        execution_role="worker",
        storage_backend="postgresql",
        database_dsn="postgresql://argus:secret@127.0.0.1:5432/argus",
        overpass_url="https://overpass.example/api/interpreter",
    )

    assert effective_map_settings(server).overpass_url == SERVER_DEFAULT_OVERPASS_URL
    assert effective_map_settings(embedded).overpass_url is None
    assert effective_map_settings(explicit).overpass_url == "https://overpass.example/api/interpreter"


@pytest.mark.asyncio
async def test_auto_enabled_overpass_skips_text_only_request_without_geocoder() -> None:
    adapter = OverpassSourceAdapter(
        provider=SimpleNamespace(endpoint=SERVER_DEFAULT_OVERPASS_URL),
        snapshots=SimpleNamespace(),
        geocoder=None,
        skip_ungeocoded_discovery=True,
    )
    text_only = CollectionRequest(
        consumer="test",
        analysis_id="text-only",
        territory={"city": "Ижевск"},
        intents=["public_mentions"],
    )

    assert await adapter.discover(text_only) == []


@pytest.mark.asyncio
async def test_auto_enabled_overpass_inventories_places_and_streets_inside_radius() -> None:
    adapter = OverpassSourceAdapter(
        provider=SimpleNamespace(endpoint=SERVER_DEFAULT_OVERPASS_URL),
        snapshots=SimpleNamespace(),
        geocoder=None,
        skip_ungeocoded_discovery=True,
    )

    tasks = await adapter.discover(request())

    assert [task.goal for task in tasks] == [
        "area_entity_inventory",
        "area_street_inventory",
    ]
    by_goal = {task.goal: task for task in tasks}
    entity_payload = by_goal["area_entity_inventory"].metadata["map_request"]
    street_payload = by_goal["area_street_inventory"].metadata["map_request"]

    assert entity_payload["radius_meters"] == 1000
    assert street_payload["radius_meters"] == 1000
    assert entity_payload["categories"] == ["named_feature"]
    assert street_payload["categories"] == ["street_feature"]
    assert entity_payload["territory"]["point"] == {
        "latitude": 56.8527,
        "longitude": 53.2115,
    }
    assert street_payload["territory"]["point"] == {
        "latitude": 56.8527,
        "longitude": 53.2115,
    }