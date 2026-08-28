from __future__ import annotations

from urllib.parse import urlparse

import pytest

from argus.contracts.models import CollectionRequest
from argus.research.planner import ResearchPlan
from argus.research.residential_sources import CuratedResidentialResearchPlanner


class _Delegate:
    def __init__(self) -> None:
        self.calls: list[CollectionRequest] = []

    async def plan(self, request: CollectionRequest) -> ResearchPlan:
        self.calls.append(request)
        return ResearchPlan(queries=["delegate query"])


def _request(*intents: str, address: str | None = "Комсомольский проспект, 27") -> CollectionRequest:
    return CollectionRequest(
        consumer="residential-direct-entry-test",
        analysis_id="residential-direct-entry-test",
        territory={"city": "Пермь", "address": address},
        intents=list(intents),
    )


@pytest.mark.asyncio
async def test_residential_plan_enters_mandatory_source_before_search_provider_fallback():
    delegate = _Delegate()
    planner = CuratedResidentialResearchPlanner(delegate)

    plan = await planner.plan(
        _request("residential_population", "residential_premises_count")
    )

    assert delegate.calls == []
    assert len(plan.tasks) == 1
    task = plan.tasks[0]
    parsed = urlparse(task.url)
    assert parsed.scheme == "https"
    assert parsed.hostname == "dom.mingkh.ru"
    assert parsed.path == "/robots.txt"
    assert parsed.query == ""
    assert task.source_id == "site_discovery"
    assert task.metadata["site_discovery_kind"] == "robots"
    assert task.metadata["root_origin"] == "https://dom.mingkh.ru"
    assert task.metadata["site_discovery_target_source_id"] == "mingkh_residential"
    assert task.metadata["dedicated_source_direct_entry"] is True
    assert task.metadata["dedicated_source_navigation"] == "robots_sitemap"
    assert task.metadata["source_owned_navigation"] is True
    assert task.metadata["research_input_scope"] == "territory_context"
    assert task.metadata["research_input_candidates"] == [
        "Пермь, Комсомольский проспект, 27",
        "Комсомольский проспект, 27",
        "Пермь",
    ]

    # Search-provider site: queries are fallback navigation and must not compete for the
    # initial page budget with the source's own robots/sitemap path.
    assert plan.queries == []
    assert all("/search" not in item.url for item in plan.tasks)
    assert any("search_provider=followup_only" in note for note in plan.notes)


@pytest.mark.asyncio
async def test_residential_source_planner_fails_closed_without_building_address():
    delegate = _Delegate()
    planner = CuratedResidentialResearchPlanner(delegate)

    plan = await planner.plan(_request("residential_population", address=None))

    assert delegate.calls == []
    assert plan.tasks == []
    assert plan.queries == []
    assert any("building_address_required=true" in note for note in plan.notes)
    assert any("direct_entry=false" in note for note in plan.notes)


@pytest.mark.asyncio
async def test_mixed_plan_keeps_direct_residential_task_and_only_delegated_queries():
    delegate = _Delegate()
    planner = CuratedResidentialResearchPlanner(delegate)

    plan = await planner.plan(_request("residential_population", "local_news"))

    assert len(delegate.calls) == 1
    assert delegate.calls[0].intents == ["local_news"]
    assert len(plan.tasks) == 1
    assert plan.tasks[0].source_id == "site_discovery"
    assert plan.tasks[0].metadata["site_discovery_target_source_id"] == "mingkh_residential"
    assert plan.queries == ["delegate query"]
    assert not any(query.startswith("site:dom.mingkh.ru") for query in plan.queries)
