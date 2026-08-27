from __future__ import annotations

from urllib.parse import parse_qs, urlparse

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


def _request(*intents: str) -> CollectionRequest:
    return CollectionRequest(
        consumer="residential-direct-entry-test",
        analysis_id="residential-direct-entry-test",
        territory={"city": "Пермь", "address": "Комсомольский проспект, 27"},
        intents=list(intents),
    )


@pytest.mark.asyncio
async def test_residential_plan_contains_direct_same_domain_house_search_task():
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
    assert parsed.path == "/search"
    assert parse_qs(parsed.query) == {
        "address": ["Пермь, Комсомольский проспект, 27"],
        "searchtype": ["house"],
    }
    assert task.source_id == "mingkh_residential"
    assert task.metadata["dedicated_source_direct_entry"] is True
    assert task.metadata["research_input_scope"] == "territory_context"
    assert task.metadata["research_input_candidates"] == [
        "Пермь, Комсомольский проспект, 27",
        "Комсомольский проспект, 27",
        "Пермь",
    ]

    assert len(plan.queries) == 2
    assert all(query.startswith("site:dom.mingkh.ru") for query in plan.queries)
    assert any("Количество жителей" in query for query in plan.queries)
    assert any("Количество квартир" in query for query in plan.queries)


@pytest.mark.asyncio
async def test_mixed_plan_keeps_direct_mingkh_task_and_delegates_other_intents():
    delegate = _Delegate()
    planner = CuratedResidentialResearchPlanner(delegate)

    plan = await planner.plan(_request("residential_population", "local_news"))

    assert len(delegate.calls) == 1
    assert delegate.calls[0].intents == ["local_news"]
    assert len(plan.tasks) == 1
    assert plan.tasks[0].source_id == "mingkh_residential"
    assert any(query.startswith("site:dom.mingkh.ru") for query in plan.queries)
    assert "delegate query" in plan.queries
