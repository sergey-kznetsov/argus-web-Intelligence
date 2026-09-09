from __future__ import annotations

from argus.contracts.models import (
    CollectionRecord,
    CollectionRequest,
    CollectionStatus,
    Evidence,
    EvidenceSource,
    Observation,
    utcnow,
)
from argus.research.lane_coverage import (
    PUBLIC_MAP_IDS,
    SOURCE_CONTOUR_IDS,
    build_research_lane_coverage,
)


def kraken_request() -> CollectionRequest:
    return CollectionRequest(
        consumer="kraken.development.uds",
        consumer_profile_version=1,
        capability="urban_signals",
        analysis_id="coverage-7-plus-3",
        territory={
            "city": "Ижевск",
            "address": "Пушкинская улица, 277",
            "point": {"latitude": 56.8527, "longitude": 53.2115},
            "radius_meters": 1200,
            "metadata": {
                "region": "Удмуртская Республика",
                "street": "Пушкинская улица",
                "house": "277",
            },
        },
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


def lane_state(
    status: str,
    *,
    processing_complete: bool = True,
    queries: list[str] | None = None,
    destinations_selected: int = 0,
    processed_pages: int = 0,
    blocked: bool = False,
    blocked_pages: int = 0,
    errors_added: int = 0,
    truncated_tasks: int = 0,
    error_codes: list[str] | None = None,
) -> dict[str, object]:
    return {
        "attempted": True,
        "processing_complete": processing_complete,
        "status": status,
        "queries": queries or [],
        "destinations_selected": destinations_selected,
        "processed_pages": processed_pages,
        "blocked": blocked,
        "blocked_pages": blocked_pages,
        "errors_added": errors_added,
        "truncated_tasks": truncated_tasks,
        "error_codes": error_codes or [],
        "providers_attempted": ["test"],
        "stop_reason": "test",
    }


def observation(
    observation_id: str,
    *,
    contour: str | None = None,
    public_map: str | None = None,
) -> Observation:
    provenance: dict[str, object] = {}
    if contour is not None:
        provenance["source_contour"] = {"contour_id": contour}
    if public_map is not None:
        provenance["public_map_source"] = {"provider": public_map}
    return Observation(
        observation_id=observation_id,
        collection_id="collection-coverage",
        analysis_id="coverage-7-plus-3",
        consumer="kraken.development.uds",
        source="generic_web",
        source_kind="web_page",
        url=f"https://example.test/{observation_id}",
        entity_type="document",
        text="source-backed text",
        content_hash=f"hash-{observation_id}",
        provenance=provenance,
    )


def evidence_item(
    evidence_id: str,
    observation_id: str,
    *,
    contour: str | None = None,
    public_map: str | None = None,
) -> Evidence:
    metadata: dict[str, object] = {}
    if contour is not None:
        metadata["source_contour"] = {"contour_id": contour}
    if public_map is not None:
        metadata["public_map_source"] = {"provider": public_map}
    return Evidence(
        evidence_id=evidence_id,
        observation_id=observation_id,
        type="document",
        text="source-backed evidence",
        source=EvidenceSource(
            provider="generic_web",
            url=f"https://example.test/{observation_id}",
            collected_at=utcnow(),
            source_id="generic_web",
        ),
        metadata=metadata,
    )


def test_research_lane_coverage_reports_exact_7_plus_3_order_and_counts() -> None:
    source_states = {
        "official_government": lane_state(
            "no_results",
            queries=["official one", "official two"],
        ),
        "public_appeals": lane_state("blocked", blocked=True),
        "housing_utilities": lane_state(
            "degraded",
            error_codes=["SEARCH_UPSTREAM_ERROR"],
        ),
        "local_forums": lane_state("completed", destinations_selected=1, processed_pages=1),
        "local_media": lane_state(
            "completed",
            queries=["local media"],
            destinations_selected=1,
            processed_pages=1,
        ),
        "public_communities": lane_state(
            "completed_with_warnings",
            destinations_selected=1,
            processed_pages=1,
            blocked_pages=1,
        ),
        "general_web": lane_state(
            "discovered",
            processing_complete=False,
            destinations_selected=1,
        ),
    }
    map_states = {
        "yandex_maps_web": lane_state("no_results"),
        "2gis_web": lane_state("completed", destinations_selected=1, processed_pages=1),
        "google_maps_web": lane_state(
            "completed_with_warnings",
            destinations_selected=1,
            processed_pages=1,
            blocked_pages=1,
        ),
    }
    timestamp = utcnow()
    record = CollectionRecord(
        collection_id="collection-coverage",
        request=kraken_request(),
        status=CollectionStatus.RUNNING,
        created_at=timestamp,
        updated_at=timestamp,
        checkpoint={
            "source_contours": source_states,
            "serial_public_map_lanes": map_states,
            "public_map_queries": [
                'site:yandex.ru/maps "Ижевск" "Пушкинская улица" отзывы'
            ],
        },
    )
    observations = [
        observation("media-1", contour="local_media"),
        observation("community-1", contour="public_communities"),
        observation("2gis-1", public_map="2gis_web"),
    ]
    evidence = [
        evidence_item("ev-media", "media-1", contour="local_media"),
        evidence_item("ev-community", "community-1", contour="public_communities"),
        evidence_item("ev-2gis", "2gis-1", public_map="2gis_web"),
    ]

    payload = build_research_lane_coverage(record, observations, evidence)

    assert payload["applicable"] is True
    assert payload["expected_lanes"] == 10
    assert payload["reported_lanes"] == 10
    assert payload["strict_order"] == [*SOURCE_CONTOUR_IDS, *PUBLIC_MAP_IDS]
    assert payload["complete"] is False

    rows = {row["lane_id"]: row for row in payload["coverage"]}
    assert rows["official_government"]["status"] == "no_data"
    assert rows["official_government"]["queries"] == 2
    assert rows["public_appeals"]["status"] == "blocked"
    assert rows["housing_utilities"]["status"] == "failed"
    assert rows["local_forums"]["status"] == "no_data"
    assert rows["local_media"]["status"] == "completed"
    assert rows["local_media"]["fetched"] == 1
    assert rows["local_media"]["observations"] == 1
    assert rows["local_media"]["evidence"] == 1
    assert rows["public_communities"]["status"] == "partial"
    assert rows["public_communities"]["warnings"] == 1
    assert rows["general_web"]["status"] == "partial"
    assert rows["yandex_maps_web"]["queries"] == 1
    assert rows["yandex_maps_web"]["status"] == "no_data"
    assert rows["2gis_web"]["status"] == "completed"
    assert rows["2gis_web"]["observations"] == 1
    assert rows["2gis_web"]["evidence"] == 1
    assert rows["google_maps_web"]["status"] == "blocked"


def test_non_urban_collection_has_no_7_plus_3_contract() -> None:
    request = CollectionRequest(
        consumer="legacy.consumer",
        analysis_id="legacy-analysis",
        territory={"city": "Ижевск"},
        intents=["documents"],
    )
    timestamp = utcnow()
    record = CollectionRecord(
        collection_id="legacy-collection",
        request=request,
        status=CollectionStatus.COMPLETED,
        created_at=timestamp,
        updated_at=timestamp,
    )

    payload = build_research_lane_coverage(record, [], [])

    assert payload["applicable"] is False
    assert payload["coverage"] == []
    assert payload["expected_lanes"] == 0
