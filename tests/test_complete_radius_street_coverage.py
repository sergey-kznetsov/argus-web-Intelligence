from __future__ import annotations

from datetime import UTC, datetime

import pytest

from argus.contracts.models import (
    CollectionRecord,
    CollectionRequest,
    CollectionStatus,
    Observation,
)
from argus.research.discovery import DiscoveryService
from argus.research.lane_coverage import (
    PUBLIC_MAP_IDS,
    SOURCE_CONTOUR_IDS,
    build_research_lane_coverage,
)
from argus.research.public_map_sources import PublicMapSourceResearchPlanner
from argus.research.radius_scope import nearby_radius_street_names
from argus.research.source_contours import SourceContourResearchPlanner
from argus.security.urls import UrlGuard


STREET_NAMES = tuple(f"улица Тестовая {index:02d}" for index in range(12))


def kraken_request() -> CollectionRequest:
    return CollectionRequest(
        consumer="kraken.development.uds",
        consumer_profile_version=1,
        capability="urban_signals",
        requested_facts=["complaint"],
        analysis_id="complete-radius-streets",
        territory={
            "city": "Ижевск",
            "address": f"{STREET_NAMES[0]}, 1",
            "point": {"latitude": 56.866315, "longitude": 53.207313},
            "radius_meters": 1200,
            "metadata": {
                "region": "Удмуртская Республика",
                "street": STREET_NAMES[0],
                "house": "1",
                "precision": "house",
            },
        },
        intents=["comments", "complaints", "discussions", "local_news"],
        constraints={"language": "ru", "max_pages": 30},
    )


def street_observations(request: CollectionRequest) -> list[Observation]:
    return [
        Observation(
            observation_id=f"street-{index}",
            collection_id="street-inventory",
            analysis_id=request.analysis_id,
            consumer=request.consumer,
            source="openstreetmap_overpass",
            source_kind="map_place",
            url=f"https://www.openstreetmap.org/way/{index + 1}",
            entity_type="place",
            title=name,
            data={"name": name, "categories": ["highway:residential"]},
            geo={
                "latitude": 56.866315 + index * 0.0001,
                "longitude": 53.207313,
            },
            content_hash=f"street-hash-{index}",
        )
        for index, name in enumerate(STREET_NAMES)
    ]


def test_complete_radius_inventory_preserves_more_than_eight_streets() -> None:
    request = kraken_request()
    observations = street_observations(request)

    assert len(nearby_radius_street_names(request, observations)) == 8
    assert nearby_radius_street_names(request, observations, limit=None) == list(STREET_NAMES)


def test_source_contours_cover_every_named_radius_street() -> None:
    request = kraken_request()
    observations = street_observations(request)

    plans = SourceContourResearchPlanner().plans(
        request,
        planner_policy="urban_signals",
        observations=observations,
    )

    assert len(plans) == 7
    for plan in plans:
        assert plan.street_names == STREET_NAMES
        assert plan.max_destinations >= len(STREET_NAMES)
        combined = "\n".join(plan.queries)
        for street in STREET_NAMES:
            assert street in combined


class RecordingProvider:
    name = "recording"

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def discover(self, queries, request):
        del request
        self.calls.append(list(queries))
        return []

    async def health(self):
        return {"status": "ok"}


@pytest.mark.asyncio
async def test_discovery_batches_all_queries_without_global_truncation() -> None:
    provider = RecordingProvider()
    service = DiscoveryService(
        providers=[provider],
        url_guard=UrlGuard.from_strings([]),
        max_queries=4,
    )
    queries = [f"query-{index}" for index in range(11)]

    outcome = await service.discover(queries, kraken_request())

    assert provider.calls == [queries[:4], queries[4:8], queries[8:]]
    assert outcome.queries_requested == 11
    assert outcome.queries_attempted == 11
    assert outcome.query_batches == 3
    assert outcome.query_batches_attempted == 3
    assert outcome.stop_reason == "no_valid_destinations"


def test_mandatory_public_maps_create_one_entry_task_per_street_per_provider() -> None:
    request = kraken_request()
    observations = street_observations(request)
    planner = PublicMapSourceResearchPlanner()

    expected_anchors = [f"Ижевск, {street}" for street in STREET_NAMES]
    assert planner.mandatory_street_anchors(request, observations) == expected_anchors

    providers = {
        "yandex_maps_web": "https://yandex.ru/maps/",
        "2gis_web": "https://2gis.ru/search/",
        "google_maps_web": "https://www.google.com/maps/search/",
    }
    for provider, prefix in providers.items():
        tasks = planner.mandatory_navigation_tasks(
            request,
            provider_id=provider,
            observations=observations,
        )
        assert len(tasks) == len(STREET_NAMES)
        assert [task.metadata["public_map_anchor"] for task in tasks] == expected_anchors
        assert all(task.url.startswith(prefix) for task in tasks)
        assert all(task.metadata["public_map_provider"] == provider for task in tasks)
        assert all(
            task.metadata["public_map_mandatory_street_scope"] is True
            for task in tasks
        )


def _lane_state(*, street_complete: bool = True, processed: int = 12) -> dict[str, object]:
    return {
        "attempted": True,
        "processing_complete": True,
        "status": "completed",
        "queries": ["query"],
        "destinations_selected": 1,
        "processed_pages": processed,
        "blocked": False,
        "blocked_pages": 0,
        "errors_added": 0,
        "truncated_tasks": 0,
        "error_codes": [],
        "providers_attempted": ["test"],
        "stop_reason": "test",
        "street_anchors_expected": len(STREET_NAMES),
        "street_anchors_attempted": len(STREET_NAMES),
        "street_anchors_processed": processed,
        "street_scope_complete": street_complete,
    }


def test_lane_coverage_marks_incomplete_map_street_scope_partial() -> None:
    request = kraken_request()
    timestamp = datetime.now(UTC)
    source_states = {lane: _lane_state() for lane in SOURCE_CONTOUR_IDS}
    map_states = {lane: _lane_state() for lane in PUBLIC_MAP_IDS}
    map_states["2gis_web"] = _lane_state(street_complete=False, processed=7)
    record = CollectionRecord(
        collection_id="complete-radius-coverage",
        request=request,
        status=CollectionStatus.RUNNING,
        created_at=timestamp,
        updated_at=timestamp,
        checkpoint={
            "radius_street_inventory": {
                "status": "completed",
                "street_names": list(STREET_NAMES),
            },
            "source_contours": source_states,
            "serial_public_map_lanes": map_states,
        },
    )

    payload = build_research_lane_coverage(record, [], [])
    rows = {row["lane_id"]: row for row in payload["coverage"]}

    assert payload["territory_scope"]["street_count"] == len(STREET_NAMES)
    assert payload["territory_scope"]["street_names"] == list(STREET_NAMES)
    assert rows["2gis_web"]["status"] == "partial"
    assert rows["2gis_web"]["street_scope"] == {
        "expected": 12,
        "attempted": 12,
        "processed": 7,
        "complete": False,
    }
