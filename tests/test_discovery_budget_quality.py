from __future__ import annotations

import pytest

from argus.contracts.models import CollectionRequest
from argus.research.discovery import DiscoveryHit, DiscoveryService
from argus.security.urls import UrlGuard


class Provider:
    name = "quality-provider"

    async def discover(self, queries, request):
        del queries, request
        return [
            DiscoveryHit(
                url="https://priority.test/izhevsk/news",
                provider=self.name,
                title="Ижевск новость",
                rank=1,
            ),
            DiscoveryHit(
                url="https://priority.test/other",
                provider=self.name,
                title="Другой материал",
                rank=2,
            ),
            DiscoveryHit(
                url="http://secondary.test/general",
                provider=self.name,
                title="Общий материал",
                rank=1,
            ),
        ]

    async def health(self):
        return {"status": "ok"}


def service(*, historical: bool = False) -> DiscoveryService:
    return DiscoveryService(
        providers=[Provider()],
        url_guard=UrlGuard.from_strings(["priority.test", "secondary.test"]),
        historical_archive_source_id="wayback_cdx" if historical else None,
    )


@pytest.mark.asyncio
async def test_navigation_score_is_explainable_and_not_evidence_confidence():
    request = CollectionRequest(
        consumer="quality-test",
        analysis_id="quality-1",
        territory={"city": "Ижевск"},
        intents=["local_news"],
        constraints={
            "allowed_domains": ["priority.test", "secondary.test"],
            "max_pages": 10,
        },
    )

    outcome = await service().discover(["query"], request)

    first = outcome.tasks[0]
    assert first.url == "https://priority.test/izhevsk/news"
    # Navigation score is bounded to 0..100 and is not evidence confidence.
    assert first.metadata["discovery_navigation_score"] == 100
    assert first.metadata["discovery_ranking_components"] == {
        "domain_priority": 0,
        "provider_rank": 1,
        "locality_matches": 1,
        "https": True,
    }
    assert first.metadata["discovery_ranking_version"] == "discovery-ranking/1"
    assert first.metadata["discovery_telemetry_version"] == "discovery-telemetry/2"
    assert first.metadata["discovery_stop_policy"] == "first_provider_with_valid_destinations"
    assert outcome.stop_reason == "first_provider_with_valid_destinations"
    assert outcome.candidates_seen == 3
    assert outcome.valid_destinations == 3
    assert outcome.destinations_selected == 3


@pytest.mark.asyncio
async def test_historical_companions_share_the_same_total_task_budget():
    request = CollectionRequest(
        consumer="quality-test",
        analysis_id="quality-budget",
        territory={"city": "Ижевск"},
        intents=["historical_context"],
        constraints={
            "allowed_domains": ["priority.test", "secondary.test"],
            "max_pages": 3,
        },
    )

    outcome = await service(historical=True).discover(["query"], request)

    assert len(outcome.tasks) == 3
    assert [item.source_id for item in outcome.tasks] == [
        "generic_web",
        "wayback_cdx",
        "generic_web",
    ]
    assert outcome.task_budget == 3
    assert outcome.destinations_selected == 2
    assert outcome.archive_companions_skipped_budget == 1
    assert outcome.stop_reason == "task_budget_reached"


@pytest.mark.asyncio
async def test_single_page_historical_budget_prioritizes_live_factual_fetch():
    request = CollectionRequest(
        consumer="quality-test",
        analysis_id="quality-one",
        territory={"city": "Ижевск"},
        intents=["historical_context"],
        constraints={
            "allowed_domains": ["priority.test"],
            "max_pages": 1,
        },
    )

    outcome = await service(historical=True).discover(["query"], request)

    assert len(outcome.tasks) == 1
    assert outcome.tasks[0].source_id == "generic_web"
    assert outcome.archive_companions_skipped_budget == 1
    assert outcome.stop_reason == "task_budget_reached"
