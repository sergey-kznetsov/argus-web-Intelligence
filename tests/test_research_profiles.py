from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from argus.contracts.models import CollectionRequest, CollectionStatus
from argus.orchestrator.mandatory_coverage import MandatoryCoverageToolPackOrchestrator
from argus.research.discovery import DiscoveryOutcome
from argus.research.public_map_sources import PublicMapSourceResearchPlanner
from argus.research.source_contours import SourceContourResearchPlanner
from argus.research_profiles import (
    RESEARCH_PROFILE_REGISTRY,
    resolved_research_profile_from_request,
)
from argus.sources.base import SourceTask
from argus.toolpacks import TOOL_PACK_REGISTRY, activate_tool_pack


def public_context_request() -> CollectionRequest:
    return CollectionRequest(
        consumer="test",
        consumer_profile_version=1,
        capability="public_context",
        analysis_id="profile-contract",
        territory={"city": "Ижевск"},
        intents=["local_news"],
        constraints={"max_pages": 1, "max_duration_seconds": 30},
    )


def test_profile_resolves_reusable_capabilities_without_consumer_branching() -> None:
    request = public_context_request()
    profile = resolved_research_profile_from_request(request)

    assert profile is not None
    assert profile.profile_id == "test_public_context"
    assert profile.source_family_ids == ("official_government", "local_media")
    assert profile.public_map_ids == ()
    assert profile.street_inventory is False
    assert profile.completion_policy.mandatory_source_families is True
    assert request.tool_pack_id == "test.public_context"

    planner = SourceContourResearchPlanner()
    assert [
        item["contour_id"] for item in planner.catalog(profile.profile_id)
    ] == ["official_government", "local_media"]


def test_profile_registry_exposes_capability_to_source_resolution() -> None:
    profile = RESEARCH_PROFILE_REGISTRY.require("urban_signals")

    assert "spatial.radius_street_inventory" in profile.capability_ids
    assert profile.source_family_ids == (
        "official_government",
        "public_appeals",
        "housing_utilities",
        "local_forums",
        "local_media",
        "public_communities",
        "general_web",
    )
    assert profile.public_map_ids == (
        "yandex_maps_web",
        "2gis_web",
        "google_maps_web",
    )
    assert profile.required_source_ids == (
        "generic_web",
        "rss_atom",
        "json_feed",
        "site_discovery",
        "openstreetmap_overpass",
    )


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
        return DiscoveryOutcome(
            tasks=[
                SourceTask(
                    source_id="generic_web",
                    goal="local_news",
                    url=f"https://source.example/{len(queries)}",
                    depth=0,
                )
            ],
            providers_attempted=["fake_search"],
            candidates_seen=1,
            valid_destinations=1,
            destinations_selected=1,
            task_budget=1,
            stop_reason="first_provider_with_valid_destinations",
        )


class ProfileHarness(MandatoryCoverageToolPackOrchestrator):
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


@pytest.mark.asyncio
async def test_second_profile_runs_on_generic_orchestrator_without_maps() -> None:
    request = public_context_request()
    timestamp = datetime.now(UTC)
    record = SimpleNamespace(
        collection_id="profile-contract",
        request=request,
        checkpoint={},
        status=CollectionStatus.RUNNING,
        partial=False,
        progress_percent=0,
        stage="planning",
        updated_at=timestamp,
        coverage=[],
        errors=[],
    )
    harness = ProfileHarness()

    applied = await harness._apply_research_profile_execution_guard(record)
    pack = TOOL_PACK_REGISTRY.resolve(
        consumer_id="test",
        capability="public_context",
        expected_tool_pack_id="test.public_context",
        requested_tool_pack_id=request.tool_pack_id,
        requested_version=request.tool_pack_version,
    )
    with activate_tool_pack(pack):
        pending = await harness._run_serial_source_contours(record, [])
        pending = await harness._run_serial_public_maps(record, pending)
    transitioned = await harness._activate_post_mandatory_budget(record)

    assert applied is True
    assert pending == []
    assert harness.events == [
        ("source_contour", "official_government"),
        ("source_contour", "local_media"),
    ]
    assert record.checkpoint["source_contours_complete"] is True
    assert record.checkpoint["serial_public_map_complete"] is True
    assert transitioned is True
    assert record.request.constraints.max_pages == 6
    assert record.request.constraints.max_duration_seconds == 30.0
    coverage = record.checkpoint["research_lane_coverage"]
    assert coverage["expected_lanes"] == 2
    assert coverage["strict_order"] == ["official_government", "local_media"]
    assert [row["lane_id"] for row in coverage["coverage"]] == [
        "official_government",
        "local_media",
    ]
