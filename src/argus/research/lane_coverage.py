from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from argus.contracts.models import CollectionRecord, Evidence, Observation
from argus.research.public_map_sources import PUBLIC_MAP_SOURCES
from argus.research.source_contours import URBAN_SIGNAL_SOURCE_CONTOURS

RESEARCH_LANE_COVERAGE_VERSION = "research-lane-coverage/3"
SOURCE_CONTOUR_IDS = tuple(profile.contour_id for profile in URBAN_SIGNAL_SOURCE_CONTOURS)
PUBLIC_MAP_IDS = tuple(profile.source_id for profile in PUBLIC_MAP_SOURCES)


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _strings(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _count(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        return max(0, int(value))
    return 0


def _observation_lane(observation: Observation) -> tuple[str | None, str | None]:
    contour = _mapping(observation.provenance.get("source_contour"))
    contour_id = str(contour.get("contour_id") or "").strip() or None
    public_map = _mapping(observation.provenance.get("public_map_source"))
    provider = str(public_map.get("provider") or "").strip() or None
    return contour_id, provider


def _evidence_lane(evidence: Evidence) -> tuple[str | None, str | None]:
    contour = _mapping(evidence.metadata.get("source_contour"))
    contour_id = str(contour.get("contour_id") or "").strip() or None
    public_map = _mapping(evidence.metadata.get("public_map_source"))
    provider = str(public_map.get("provider") or "").strip() or None
    return contour_id, provider


def _meaningful_error_codes(state: Mapping[str, Any]) -> list[str]:
    return [
        code
        for code in _strings(state.get("error_codes"))
        if code != "DISCOVERY_NO_RESULTS"
    ]


def _normalized_status(
    state: Mapping[str, Any],
    *,
    observations: int,
    evidence: int,
) -> str:
    if not state or state.get("processing_complete") is not True:
        return "partial"

    raw_status = str(state.get("status") or "").strip().casefold()
    blocked_pages = _count(state.get("blocked_pages"))
    truncated_tasks = _count(state.get("truncated_tasks"))
    errors_added = _count(state.get("errors_added"))
    error_codes = _meaningful_error_codes(state)
    blocked = state.get("blocked") is True or blocked_pages > 0
    errors = errors_added + len(error_codes)
    warnings = blocked_pages + truncated_tasks
    fetched = _count(state.get("processed_pages"))
    street_scope_incomplete = state.get("street_scope_complete") is False

    if blocked and observations == 0 and evidence == 0:
        return "blocked"
    if raw_status in {"degraded", "error", "failed"} and observations == 0 and evidence == 0:
        return "failed"
    if errors and observations == 0 and evidence == 0 and fetched == 0:
        return "failed"
    if street_scope_incomplete:
        return "partial"
    if observations == 0 and evidence == 0:
        if errors or warnings:
            return "partial"
        return "no_data"
    if errors or warnings or raw_status in {"completed_with_warnings", "partial", "degraded"}:
        return "partial"
    return "completed"


def _street_scope(state: Mapping[str, Any]) -> dict[str, object]:
    expected = _count(state.get("street_anchors_expected"))
    attempted = _count(state.get("street_anchors_attempted"))
    processed = _count(state.get("street_anchors_processed"))
    complete_raw = state.get("street_scope_complete")
    complete = complete_raw if isinstance(complete_raw, bool) else None
    result: dict[str, object] = {
        "expected": expected,
        "attempted": attempted,
        "processed": processed,
        "complete": complete,
    }
    telemetry = str(state.get("street_scope_telemetry") or "").strip()
    if telemetry:
        result["telemetry"] = telemetry
    return result


def _row(
    *,
    lane_id: str,
    lane_kind: str,
    state: Mapping[str, Any],
    query_count: int,
    observations: int,
    evidence: int,
) -> dict[str, object]:
    error_codes = _meaningful_error_codes(state)
    blocked_pages = _count(state.get("blocked_pages"))
    truncated_tasks = _count(state.get("truncated_tasks"))
    errors_added = _count(state.get("errors_added"))
    return {
        "lane_id": lane_id,
        "lane_kind": lane_kind,
        "status": _normalized_status(
            state,
            observations=observations,
            evidence=evidence,
        ),
        "queries": max(0, int(query_count)),
        "queries_attempted": _count(state.get("queries_attempted")),
        "query_batches": _count(state.get("query_batches")),
        "query_batches_attempted": _count(state.get("query_batches_attempted")),
        "discovered": _count(state.get("destinations_selected")),
        # The serial executor exposes processed_pages rather than a separate successful-fetch
        # counter. This field therefore means pages consumed by the bounded fetch/extract lane,
        # including pages that later proved blocked or erroneous.
        "fetched": _count(state.get("processed_pages")),
        "observations": max(0, int(observations)),
        "evidence": max(0, int(evidence)),
        "warnings": blocked_pages + truncated_tasks,
        "errors": errors_added + len(error_codes),
        "providers_attempted": _strings(state.get("providers_attempted")),
        "error_codes": error_codes,
        "stop_reason": str(state.get("stop_reason") or "").strip() or None,
        "processing_complete": state.get("processing_complete") is True,
        "street_scope": _street_scope(state),
    }


def _source_state_with_street_scope(
    state: Mapping[str, Any],
    *,
    street_count: int,
) -> dict[str, Any]:
    """Project street coverage without inventing progress from lane completion.

    Older checkpoints did not persist source-contour street telemetry. The previous
    projection treated a clean lane completion as proof that every radius street had been
    attempted and processed. That can create false 100% coverage. Coverage now fails closed:
    only explicit checkpoint counters (or explicit discovery query-attempt telemetry for the
    attempted count) are accepted as proof of street progress.
    """

    normalized = dict(state)
    if street_count <= 0:
        return normalized

    expected = _count(normalized.get("street_anchors_expected")) or street_count
    normalized["street_anchors_expected"] = expected

    explicit_attempted = "street_anchors_attempted" in normalized
    explicit_processed = "street_anchors_processed" in normalized
    explicit_complete = isinstance(normalized.get("street_scope_complete"), bool)

    if explicit_attempted:
        attempted = min(expected, _count(normalized.get("street_anchors_attempted")))
        attempted_source = "explicit_checkpoint"
    elif "queries_attempted" in normalized:
        # SourceContourResearchPlanner emits one street query per radius street first,
        # followed by generic contour queries. Therefore the bounded attempted-query count
        # can conservatively prove at most this many street anchors were attempted.
        attempted = min(expected, _count(normalized.get("queries_attempted")))
        attempted_source = "discovery_queries_attempted"
    else:
        attempted = 0
        attempted_source = "missing_checkpoint"

    if explicit_processed:
        processed = min(attempted, _count(normalized.get("street_anchors_processed")))
        processed_source = "explicit_checkpoint"
    else:
        processed = 0
        processed_source = "missing_checkpoint"

    requested_complete = normalized.get("street_scope_complete") is True
    complete = bool(
        explicit_complete
        and requested_complete
        and attempted >= expected
        and processed >= expected
    )

    normalized["street_anchors_attempted"] = attempted
    normalized["street_anchors_processed"] = processed
    normalized["street_scope_complete"] = complete
    normalized["street_scope_telemetry"] = (
        "explicit_checkpoint"
        if explicit_attempted and explicit_processed and explicit_complete
        else f"attempted:{attempted_source};processed:{processed_source}"
    )
    return normalized


def build_research_lane_coverage(
    record: CollectionRecord,
    observations: Sequence[Observation],
    evidence: Sequence[Evidence],
) -> dict[str, object]:
    """Build deterministic 7+3 diagnostics from serial-lane checkpoint + provenance.

    This is operational telemetry only. It does not create Evidence, change factual source
    semantics, or replace the existing per-task ``SourceCoverage`` contract.
    """

    checkpoint = record.checkpoint
    source_states = _mapping(checkpoint.get("source_contours"))
    map_states = _mapping(checkpoint.get("serial_public_map_lanes"))
    applicable = (
        record.request.capability == "urban_signals"
        or bool(source_states)
        or bool(map_states)
    )
    if not applicable:
        return {
            "version": RESEARCH_LANE_COVERAGE_VERSION,
            "collection_id": record.collection_id,
            "applicable": False,
            "expected_lanes": 0,
            "reported_lanes": 0,
            "complete": True,
            "coverage": [],
            "status_counts": {},
        }

    street_inventory = _mapping(checkpoint.get("radius_street_inventory"))
    street_names = _strings(street_inventory.get("street_names"))
    street_count = len(street_names)

    observation_contours: Counter[str] = Counter()
    observation_maps: Counter[str] = Counter()
    evidence_contours: Counter[str] = Counter()
    evidence_maps: Counter[str] = Counter()

    for item in observations:
        contour_id, provider = _observation_lane(item)
        if contour_id:
            observation_contours[contour_id] += 1
        if provider:
            observation_maps[provider] += 1

    for item in evidence:
        contour_id, provider = _evidence_lane(item)
        if contour_id:
            evidence_contours[contour_id] += 1
        if provider:
            evidence_maps[provider] += 1

    rows: list[dict[str, object]] = []
    for contour_id in SOURCE_CONTOUR_IDS:
        raw_state = _mapping(source_states.get(contour_id))
        state = _source_state_with_street_scope(
            raw_state,
            street_count=street_count,
        )
        rows.append(
            _row(
                lane_id=contour_id,
                lane_kind="source_contour",
                state=state,
                query_count=len(_strings(state.get("queries"))),
                observations=observation_contours[contour_id],
                evidence=evidence_contours[contour_id],
            )
        )

    public_map_queries = _strings(checkpoint.get("public_map_queries"))
    for provider in PUBLIC_MAP_IDS:
        state = _mapping(map_states.get(provider))
        query_count = 0
        if provider == "yandex_maps_web":
            query_count = sum(1 for query in public_map_queries if "yandex.ru/maps" in query)
        rows.append(
            _row(
                lane_id=provider,
                lane_kind="public_map",
                state=state,
                query_count=query_count,
                observations=observation_maps[provider],
                evidence=evidence_maps[provider],
            )
        )

    status_counts = Counter(str(row["status"]) for row in rows)
    complete = all(bool(row["processing_complete"]) for row in rows)
    return {
        "version": RESEARCH_LANE_COVERAGE_VERSION,
        "collection_id": record.collection_id,
        "applicable": True,
        "expected_lanes": len(SOURCE_CONTOUR_IDS) + len(PUBLIC_MAP_IDS),
        "reported_lanes": len(rows),
        "complete": complete,
        "strict_order": [*SOURCE_CONTOUR_IDS, *PUBLIC_MAP_IDS],
        "territory_scope": {
            "street_inventory_status": str(street_inventory.get("status") or "").strip()
            or None,
            "street_count": street_count,
            "street_names": street_names,
        },
        "counter_semantics": {
            "queries": "discovery queries requested for the lane; direct map navigation uses zero",
            "queries_attempted": "discovery queries whose batch was actually attempted",
            "query_batches": "bounded discovery batches planned for the lane",
            "query_batches_attempted": "bounded discovery batches actually attempted",
            "discovered": "destinations selected for the lane",
            "fetched": "pages consumed by the bounded serial fetch/extract lane",
            "observations": "stored observations carrying this lane provenance",
            "evidence": "stored evidence carrying this lane provenance",
            "warnings": "blocked pages plus truncated queued tasks",
            "errors": "runtime lane errors plus non-empty discovery error codes",
            "street_scope": (
                "named radius streets expected, attempted and processed by the mandatory lane; "
                "missing source-contour telemetry is never inferred as complete"
            ),
        },
        "coverage": rows,
        "status_counts": dict(sorted(status_counts.items())),
    }


__all__ = [
    "PUBLIC_MAP_IDS",
    "RESEARCH_LANE_COVERAGE_VERSION",
    "SOURCE_CONTOUR_IDS",
    "build_research_lane_coverage",
]
