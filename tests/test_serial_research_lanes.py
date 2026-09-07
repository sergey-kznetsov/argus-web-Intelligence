from __future__ import annotations

from types import SimpleNamespace

import pytest

from argus.contracts.models import CollectionRequest
from argus.orchestrator.toolpack_aware import (
    ToolPackAwareEvidenceStatusAdaptiveResearchOrchestrator,
)
from argus.research.discovery import DiscoveryOutcome
from argus.research.source_contours import SourceContourResearchPlanner
from argus.sources.base import SourceTask
from argus.toolpacks import TOOL_PACK_REGISTRY, activate_tool_pack


class FakeRepository:
    async def update_collection(self, record) -> None:
        del record


class FakeDiscovery:
    def __init__(self, events: list[tuple[str, str]]) -> None:
        self.events = events
        self.counter = 0

    async def discover(self, queries, request) -> DiscoveryOutcome:
        del queries, request
        self.counter += 1
        self.events.append(("discover", str(self.counter)))
        task = SourceTask(
            source_id="generic_web",
            goal="complaints",
            url=f"https://source-{self.counter}.example/page",
            depth=0,
            metadata={"research_goals": ["complaints"]},
        )
        return DiscoveryOutcome(
            tasks=[task],
            providers_attempted=["fake_search"],
            candidates_seen=1,
            valid_destinations=1,
            destinations_selected=1,
            task_budget=1,
            stop_reason="first_provider_with_valid_destinations",
        )


class SerialHarness(ToolPackAwareEvidenceStatusAdaptiveResearchOrchestrator):
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []
        self.repository = FakeRepository()
        self.discovery = FakeDiscovery(self.events)
        self.source_contour_planner = SourceContourResearchPlanner()
        self.public_map_source_planner = None

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
        del lane_tasks, deferred_pending, lane_id, lane_kind, page_limit, future_lane_count
        self.events.append(("process", lane_label))
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


def kraken_request() -> CollectionRequest:
    return CollectionRequest(
        consumer="kraken.development.uds",
        consumer_profile_version=1,
        capability="urban_signals",
        analysis_id="serial-source-families",
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


@pytest.mark.asyncio
async def test_source_families_finish_processing_before_next_discovery() -> None:
    harness = SerialHarness()
    request = kraken_request()
    record = SimpleNamespace(
        collection_id="collection-serial",
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
        pending = await harness._run_serial_source_contours(record, [])

    assert pending == []
    assert len(harness.events) == 14
    assert [kind for kind, _ in harness.events] == ["discover", "process"] * 7
    assert [label for kind, label in harness.events if kind == "process"] == [
        "official_government",
        "public_appeals",
        "housing_utilities",
        "local_forums",
        "local_media",
        "public_communities",
        "general_web",
    ]
    assert record.checkpoint["source_contours_complete"] is True
    assert record.checkpoint["serial_research_lanes"]["strict_sequential"] is True


def test_public_map_child_cannot_cross_provider_lane() -> None:
    two_gis = SourceTask(
        source_id="generic_web",
        goal="comments",
        url="https://2gis.ru/izhevsk/firm/123",
    )
    google = SourceTask(
        source_id="generic_web",
        goal="comments",
        url="https://www.google.com/maps/place/example",
    )

    assert (
        ToolPackAwareEvidenceStatusAdaptiveResearchOrchestrator._child_belongs_to_lane(
            two_gis,
            lane_kind="public_map",
            lane_label="2gis_web",
        )
        is True
    )
    assert (
        ToolPackAwareEvidenceStatusAdaptiveResearchOrchestrator._child_belongs_to_lane(
            google,
            lane_kind="public_map",
            lane_label="2gis_web",
        )
        is False
    )
