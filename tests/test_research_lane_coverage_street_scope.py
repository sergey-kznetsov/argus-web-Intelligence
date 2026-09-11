from __future__ import annotations

from argus.contracts.models import CollectionRecord, CollectionRequest, CollectionStatus, utcnow
from argus.research.lane_coverage import build_research_lane_coverage


def _record(
    source_state: dict[str, object],
    *,
    source_contour_version: str | None = None,
) -> CollectionRecord:
    request = CollectionRequest(
        consumer="kraken.development.uds",
        consumer_profile_version=1,
        capability="urban_signals",
        analysis_id="street-scope-coverage",
        territory={
            "city": "Ижевск",
            "address": "Пушкинская улица, 277",
            "point": {"latitude": 56.866315, "longitude": 53.207313},
            "radius_meters": 1200,
            "metadata": {"region": "Удмуртская Республика"},
        },
        intents=["local_news"],
        constraints={"language": "ru", "max_pages": 30},
    )
    timestamp = utcnow()
    checkpoint: dict[str, object] = {
        "radius_street_inventory": {
            "status": "completed",
            "street_names": ["Пушкинская улица", "Советская улица"],
            "streets": [
                {
                    "name": "Пушкинская улица",
                    "distance_meters": 12.5,
                    "relationship": "returned_by_radius_inventory",
                    "provider": "openstreetmap_overpass",
                    "observation_id": "obs-street-1",
                    "source_url": "https://www.openstreetmap.org/way/1",
                    "geometry_basis": "representative_point_ordering_only",
                },
                {
                    "name": "Советская улица",
                    "distance_meters": None,
                    "relationship": "returned_by_radius_inventory",
                    "provider": "openstreetmap_overpass",
                    "observation_id": "obs-street-2",
                    "source_url": "https://www.openstreetmap.org/way/2",
                    "geometry_basis": "overpass_radius_membership",
                },
            ],
        },
        "source_contours": {"official_government": source_state},
    }
    if source_contour_version is not None:
        checkpoint["source_contour_version"] = source_contour_version
    return CollectionRecord(
        collection_id="street-scope-collection",
        request=request,
        status=CollectionStatus.COMPLETED,
        created_at=timestamp,
        updated_at=timestamp,
        checkpoint=checkpoint,
    )


def _official_row(payload: dict[str, object]) -> dict[str, object]:
    rows = payload["coverage"]
    assert isinstance(rows, list)
    return next(row for row in rows if row["lane_id"] == "official_government")


def test_clean_legacy_lane_completion_does_not_invent_full_street_scope() -> None:
    payload = build_research_lane_coverage(
        _record(
            {
                "attempted": True,
                "processing_complete": True,
                "status": "completed",
                "queries": ["street query one", "street query two"],
                "processed_pages": 2,
            }
        ),
        [],
        [],
    )

    row = _official_row(payload)
    scope = row["street_scope"]
    assert payload["version"] == "research-lane-coverage/4"
    assert payload["territory_scope"]["streets"][0] == {
        "name": "Пушкинская улица",
        "distance_meters": 12.5,
        "relationship": "returned_by_radius_inventory",
        "provider": "openstreetmap_overpass",
        "observation_id": "obs-street-1",
        "source_url": "https://www.openstreetmap.org/way/1",
        "geometry_basis": "representative_point_ordering_only",
    }
    assert row["status"] == "partial"
    assert scope == {
        "expected": 2,
        "attempted": 0,
        "processed": 0,
        "complete": False,
        "telemetry": "attempted:missing_checkpoint;processed:missing_checkpoint",
    }


def test_attempted_streets_can_use_explicit_discovery_query_telemetry() -> None:
    payload = build_research_lane_coverage(
        _record(
            {
                "attempted": True,
                "processing_complete": True,
                "status": "completed",
                "queries": ["street query one", "street query two", "generic query"],
                "queries_attempted": 1,
                "query_batches": 2,
                "query_batches_attempted": 1,
                "processed_pages": 1,
            }
        ),
        [],
        [],
    )

    row = _official_row(payload)
    scope = row["street_scope"]
    assert row["queries"] == 3
    assert row["queries_attempted"] == 1
    assert row["query_batches"] == 2
    assert row["query_batches_attempted"] == 1
    assert scope["expected"] == 2
    assert scope["attempted"] == 1
    assert scope["processed"] == 0
    assert scope["complete"] is False
    assert scope["telemetry"] == (
        "attempted:discovery_queries_attempted;processed:missing_checkpoint"
    )


def test_explicit_processed_street_scope_can_be_reported_complete() -> None:
    payload = build_research_lane_coverage(
        _record(
            {
                "attempted": True,
                "processing_complete": True,
                "status": "completed",
                "queries": ["street query one", "street query two"],
                "queries_attempted": 2,
                "street_anchors_expected": 2,
                "street_anchors_attempted": 2,
                "street_anchors_processed": 2,
                "street_scope_complete": True,
                "processed_pages": 2,
            }
        ),
        [],
        [],
    )

    row = _official_row(payload)
    scope = row["street_scope"]
    assert row["status"] == "no_data"
    assert scope == {
        "expected": 2,
        "attempted": 2,
        "processed": 2,
        "complete": True,
        "telemetry": "explicit_checkpoint",
    }


def test_v6_clean_discovery_proves_every_radius_street_query_was_processed() -> None:
    payload = build_research_lane_coverage(
        _record(
            {
                "attempted": True,
                "processing_complete": True,
                "status": "completed",
                "queries": ["street query one", "street query two", "generic query"],
                "processed_pages": 0,
                "blocked": False,
                "error_codes": ["DISCOVERY_NO_RESULTS"],
            },
            source_contour_version="source-contours/6",
        ),
        [],
        [],
    )

    row = _official_row(payload)
    scope = row["street_scope"]
    assert row["queries"] == 3
    assert row["queries_attempted"] == 3
    assert row["status"] == "no_data"
    assert scope["expected"] == 2
    assert scope["attempted"] == 2
    assert scope["processed"] == 2
    assert scope["complete"] is True
    assert scope["telemetry"] == (
        "source-contours/6:attempted=source-contours/6;processed=source-contours/6"
    )


def test_v6_blocked_discovery_never_claims_complete_street_scope() -> None:
    payload = build_research_lane_coverage(
        _record(
            {
                "attempted": True,
                "processing_complete": True,
                "status": "blocked",
                "queries": ["street query one", "street query two", "generic query"],
                "processed_pages": 0,
                "blocked": True,
                "error_codes": ["DISCOVERY_BLOCKED", "DISCOVERY_INCOMPLETE"],
            },
            source_contour_version="source-contours/6",
        ),
        [],
        [],
    )

    row = _official_row(payload)
    scope = row["street_scope"]
    assert row["status"] == "blocked"
    assert row["queries_attempted"] == 0
    assert scope["attempted"] == 0
    assert scope["processed"] == 0
    assert scope["complete"] is False
