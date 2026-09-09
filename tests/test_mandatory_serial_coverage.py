from __future__ import annotations

from types import SimpleNamespace

import pytest

from argus.contracts.models import CollectionRequest, CollectionStatus
from argus.orchestrator.mandatory_coverage import MandatoryCoverageToolPackOrchestrator
from argus.research.discovery import DiscoveryOutcome
from argus.research.public_map_sources import PublicMapSourceResearchPlanner
from argus.research.source_contours import SourceContourResearchPlanner
from argus.sources.base import SourceTask
from argus.toolpacks import TOOL_PACK_REGISTRY, activate_tool_pack


SOURCE_CONTOUR_ORDER = [
    "official_government",
    "public_appeals",
    "housing_utilities",
    "local_forums",
    "local_media",
    "public_communities",
    "general_web",
]
PUBLIC_MAP_ORDER = [
    "yandex_maps_web",
    "2gis_web",
    "google_maps_web",
]


class FakeRepository:
    async def update_collection(self, record) -> None:
        del record

    async def list_observations(self, collection_id):
        del collection_id
        return []

    async def list_evidence(self, collection_id):
        del collection_id
        return []


class FakeDiscovery:
    async def discover(self, queries, request) -> DiscoveryOutcome:
        del request
        is_yandex_map = any("site:yandex.ru/maps" in query for query in queries)
        url = (
            "https://yandex.ru/maps/org/example/123"
            if is_yandex_map
            else "https://source.example/item"
        )
        return DiscoveryOutcome(
            tasks=[
                SourceTask(
                    source_id="generic_web",
                    goal="complaints",
                    url=url,
                    depth=0,
                    metadata={"research_goals": ["complaints"]},
                )
            ],
            providers_attempted=["fake_search"],
            candidates_seen=1,
            valid_destinations=1,
            destinations_selected=1,
            task_budget=1,
            stop_reason="first_provider_with_valid_destinations",
        )


class MandatorySerialHarness(MandatoryCoverageToolPackOrchestrator):
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []
        self.repository = FakeRepository()
        self.discovery = FakeDiscovery()
        self.source_contour_planner = SourceContourResearchPlanner()
        self.public_map_source_planner = PublicMapSourceResearchPlanner()
        self.source_task_timeout_seconds = 45.0

    async def _process_serial_lane(
        self,
        record,
        lane_tasks,
        deferred_pending,
        *,
        lane_id,
        lane_kind,
        lane_label,
        page_limit,
        future_lane_count,
    ):
        del lane_tasks, deferred_pending, lane_id, page_limit, future_lane_count
        self.events.append((lane_kind, lane_label))
        visited = list(record.checkpoint.get("visited", []))
        visited.append(f"visited-{len(visited) + 1}")
        record.checkpoint = {**record.checkpoint, "visited": visited}
        return record, {
            "processed_pages": 1,
            "blocked_pages": 0,
            "errors_added": 0,
            "truncated_tasks": 0,
        }

    @staticmethod
    def _task_dict(task):
        return {
            "source_id": task.source_id,
            "goal": task.goal,
            "url": task.url,
            "depth": task.depth,
            "metadata": dict(task.metadata),
        }


def kraken_request(*, max_pages: int = 1) -> CollectionRequest:
    return CollectionRequest(
        consumer="kraken.development.uds",
        consumer_profile_version=1,
        capability="urban_signals",
        analysis_id="mandatory-serial-coverage",
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
        constraints={
            "language": "ru",
            "max_pages": max_pages,
            "max_duration_seconds": 30,
        },
    )


def running_record(*, max_pages: int = 1, checkpoint=None):
    return SimpleNamespace(
        collection_id="collection-mandatory-serial",
        request=kraken_request(max_pages=max_pages),
        checkpoint=dict(checkpoint or {}),
        status=CollectionStatus.RUNNING,
        partial=False,
        progress_percent=0,
        stage="planning",
        updated_at=None,
        coverage=[],
        errors=[],
    )


def kraken_pack(request: CollectionRequest):
    return TOOL_PACK_REGISTRY.resolve(
        consumer_id="kraken.development.uds",
        capability="urban_signals",
        expected_tool_pack_id="kraken.urban_signals",
        requested_tool_pack_id=request.tool_pack_id,
        requested_version=request.tool_pack_version,
    )


@pytest.mark.asyncio
async def test_requested_max_pages_cannot_skip_mandatory_7_plus_3() -> None:
    harness = MandatorySerialHarness()
    record = running_record(max_pages=1)

    applied = await harness._apply_urban_signal_execution_guard(record)
    assert applied is True
    assert record.request.constraints.max_pages == harness.emergency_max_pages
    assert record.checkpoint["mandatory_coverage"]["requested_max_pages"] == 1

    with activate_tool_pack(kraken_pack(record.request)):
        pending = await harness._run_serial_source_contours(record, [])
        pending = await harness._run_serial_public_maps(record, pending)

    assert pending == []
    assert harness.events == [
        *[("source_contour", label) for label in SOURCE_CONTOUR_ORDER],
        *[("public_map", label) for label in PUBLIC_MAP_ORDER],
    ]
    assert record.checkpoint["source_contours_complete"] is True
    assert record.checkpoint["serial_public_map_complete"] is True
    assert len(record.checkpoint["visited"]) == 10

    transitioned = await harness._activate_post_mandatory_budget(record)
    assert transitioned is True
    assert record.checkpoint["mandatory_coverage"]["mandatory_complete"] is True
    assert record.checkpoint["mandatory_coverage"]["phase"] == "optional"
    assert record.request.constraints.max_pages == 34

    telemetry = record.checkpoint["research_lane_coverage"]
    assert telemetry["expected_lanes"] == 10
    assert telemetry["reported_lanes"] == 10
    assert telemetry["strict_order"] is True
    assert [row["lane_id"] for row in telemetry["rows"]] == [
        *SOURCE_CONTOUR_ORDER,
        *PUBLIC_MAP_ORDER,
    ]


@pytest.mark.asyncio
async def test_checkpoint_resume_skips_completed_mandatory_lanes() -> None:
    completed_contours = SOURCE_CONTOUR_ORDER[:2]
    checkpoint = {
        "source_contours": {
            label: {
                "attempted": True,
                "processing_complete": True,
                "status": "completed",
            }
            for label in completed_contours
        },
        "serial_public_map_lanes": {
            "yandex_maps_web": {
                "attempted": True,
                "processing_complete": True,
                "status": "completed",
            }
        },
    }
    harness = MandatorySerialHarness()
    record = running_record(max_pages=1, checkpoint=checkpoint)

    await harness._apply_urban_signal_execution_guard(record)
    with activate_tool_pack(kraken_pack(record.request)):
        pending = await harness._run_serial_source_contours(record, [])
        pending = await harness._run_serial_public_maps(record, pending)

    assert pending == []
    assert harness.events == [
        *[("source_contour", label) for label in SOURCE_CONTOUR_ORDER[2:]],
        ("public_map", "2gis_web"),
        ("public_map", "google_maps_web"),
    ]
    assert record.checkpoint["source_contours_complete"] is True
    assert record.checkpoint["serial_public_map_complete"] is True
    assert all(
        record.checkpoint["source_contours"][label]["processing_complete"] is True
        for label in SOURCE_CONTOUR_ORDER
    )
    assert all(
        record.checkpoint["serial_public_map_lanes"][label]["processing_complete"] is True
        for label in PUBLIC_MAP_ORDER
    )