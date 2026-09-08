from __future__ import annotations

from datetime import UTC, datetime

import pytest

from argus.contracts.models import CollectionRecord, CollectionRequest, CollectionStatus
from argus.orchestrator.mandatory_coverage import MandatoryCoverageToolPackOrchestrator
from argus.research.public_map_sources import PublicMapSourceResearchPlanner
from argus.research.source_contours import SourceContourResearchPlanner


class FakeRepository:
    def __init__(self) -> None:
        self.updated = None

    async def update_collection(self, record) -> None:
        self.updated = record


class GuardHarness(MandatoryCoverageToolPackOrchestrator):
    def __init__(self) -> None:
        self.repository = FakeRepository()
        self.source_contour_planner = SourceContourResearchPlanner()
        self.public_map_source_planner = PublicMapSourceResearchPlanner()
        self.source_task_timeout_seconds = 45.0


def urban_signal_record() -> CollectionRecord:
    request = CollectionRequest(
        consumer="kraken.development.uds",
        consumer_profile_version=1,
        capability="urban_signals",
        analysis_id="mandatory-coverage",
        territory={
            "city": "Ижевск",
            "address": "Пушкинская улица, 277",
            "point": {"latitude": 56.8527, "longitude": 53.2115},
            "radius_meters": 1200,
        },
        intents=["comments", "complaints", "public_appeals", "local_news"],
        constraints={
            "max_pages": 1,
            "max_duration_seconds": 30,
            "max_depth": 2,
        },
    )
    timestamp = datetime.now(UTC)
    return CollectionRecord(
        collection_id="collection-mandatory",
        request=request,
        status=CollectionStatus.RUNNING,
        created_at=timestamp,
        updated_at=timestamp,
    )


@pytest.mark.asyncio
async def test_urban_signal_collection_limits_become_emergency_guards() -> None:
    harness = GuardHarness()
    record = urban_signal_record()

    applied = await harness._apply_urban_signal_execution_guard(record)

    assert applied is True
    assert record.request.constraints.max_pages == 500
    assert record.request.constraints.max_duration_seconds == 7_200.0
    guard = record.checkpoint["mandatory_coverage"]
    assert guard["collection_limits_semantics"] == "emergency_guard_only"
    assert guard["requested_max_pages"] == 1
    assert guard["requested_max_duration_seconds"] == 30.0
    assert guard["source_contours"] == [
        "official_government",
        "public_appeals",
        "housing_utilities",
        "local_forums",
        "local_media",
        "public_communities",
        "general_web",
    ]
    assert guard["public_map_providers"] == [
        "yandex_maps_web",
        "2gis_web",
        "google_maps_web",
    ]


def test_urban_signal_mandatory_lanes_ignore_collection_page_exhaustion() -> None:
    record = urban_signal_record()
    record.checkpoint = {"visited": [f"page-{index}" for index in range(20)]}

    assert MandatoryCoverageToolPackOrchestrator._execution_budget_exhausted(record) is False


def test_other_consumers_keep_normal_collection_budget_semantics() -> None:
    request = CollectionRequest(
        consumer="legacy.test",
        analysis_id="legacy-budget",
        territory={"city": "Ижевск"},
        intents=["comments"],
        constraints={"max_pages": 1, "max_duration_seconds": 30},
    )
    timestamp = datetime.now(UTC)
    record = CollectionRecord(
        collection_id="legacy-budget",
        request=request,
        status=CollectionStatus.RUNNING,
        created_at=timestamp,
        updated_at=timestamp,
        checkpoint={"visited": ["already-processed"]},
    )

    assert MandatoryCoverageToolPackOrchestrator._execution_budget_exhausted(record) is True
