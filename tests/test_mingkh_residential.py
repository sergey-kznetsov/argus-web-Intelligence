from __future__ import annotations

from types import SimpleNamespace

import pytest

from argus.contracts.models import CollectionRequest
from argus.crawler.models import FetchResult
from argus.research.followup import FollowupPlan
from argus.research.planner import ResearchPlan
from argus.research.residential_sources import (
    CuratedResidentialFollowupResearchPlanner,
    CuratedResidentialResearchPlanner,
)
from argus.sources.base import SourceTask
from argus.sources.mingkh_residential import MingkhResidentialAdapter


class _FailPlanner:
    async def plan(self, request: CollectionRequest) -> ResearchPlan:
        raise AssertionError(f"delegate must not receive residential-only request: {request.intents}")


class _RecordingPlanner:
    def __init__(self) -> None:
        self.intents: list[str] = []

    async def plan(self, request: CollectionRequest) -> ResearchPlan:
        self.intents = list(request.intents)
        return ResearchPlan(queries=['"Пермь" новости'])


class _FailFollowupPlanner:
    async def plan_followups(self, request, observations, *, seen_queries, max_queries):
        raise AssertionError(f"delegate must not receive residential-only request: {request.intents}")


class _RecordingFollowupPlanner:
    def __init__(self) -> None:
        self.intents: list[str] = []

    async def plan_followups(self, request, observations, *, seen_queries, max_queries):
        self.intents = list(request.intents)
        return FollowupPlan(queries=['"Пермь" местные новости'])


class _Snapshots:
    async def capture(self, *args, **kwargs):
        return SimpleNamespace(snapshot_id="snapshot-test")


class _Web:
    async def health(self):
        return {"status": "ok"}



def _request(*intents: str, address: str = "Комсомольский проспект, 27") -> CollectionRequest:
    return CollectionRequest(
        consumer="consumer-neutral-test",
        analysis_id="analysis-1",
        territory={"city": "Пермь", "address": address},
        intents=list(intents),
    )


def _task() -> SourceTask:
    return SourceTask(
        source_id="mingkh_residential",
        goal="residential_premises_count",
        url="https://dom.mingkh.ru/perm/perm/123456",
        metadata={"collection_id": "collection-1"},
    )


def _fetched(html: str, *, blocked: bool = False) -> FetchResult:
    return FetchResult(
        url="https://dom.mingkh.ru/perm/perm/123456",
        final_url="https://dom.mingkh.ru/perm/perm/123456",
        status_code=200,
        content_type="text/html; charset=utf-8",
        text=html,
        title="Дом",
        blocked=blocked,
        runtime="browser",
    )


@pytest.mark.asyncio
async def test_residential_only_plan_uses_only_mandatory_mingkh_source():
    planner = CuratedResidentialResearchPlanner(_FailPlanner(), max_queries=8)
    plan = await planner.plan(
        _request("residential_population", "residential_premises_count")
    )

    assert len(plan.queries) == 2
    assert all("site:dom.mingkh.ru" in query for query in plan.queries)
    assert any("Количество квартир" in query for query in plan.queries)
    assert any("Количество жителей" in query for query in plan.queries)
    assert any("fallback_sources=false" in note for note in plan.notes)


@pytest.mark.asyncio
async def test_mixed_plan_hides_residential_intents_from_general_planner():
    delegate = _RecordingPlanner()
    planner = CuratedResidentialResearchPlanner(delegate, max_queries=8)
    plan = await planner.plan(_request("residential_population", "local_news"))

    assert delegate.intents == ["local_news"]
    assert plan.queries[0].startswith("site:dom.mingkh.ru")
    assert '"Пермь" новости' in plan.queries


@pytest.mark.asyncio
async def test_residential_followup_never_asks_general_planner_for_residential_gap():
    planner = CuratedResidentialFollowupResearchPlanner(_FailFollowupPlanner())
    plan = await planner.plan_followups(
        _request("residential_population"),
        [],
        seen_queries=set(),
        max_queries=4,
    )

    assert len(plan.queries) == 1
    assert plan.queries[0].startswith("site:dom.mingkh.ru")
    assert "Количество жителей" in plan.queries[0]


@pytest.mark.asyncio
async def test_mixed_followup_delegates_only_nonresidential_intents():
    delegate = _RecordingFollowupPlanner()
    planner = CuratedResidentialFollowupResearchPlanner(delegate)
    plan = await planner.plan_followups(
        _request("residential_premises_count", "local_news"),
        [],
        seen_queries=set(),
        max_queries=4,
    )

    assert delegate.intents == ["local_news"]
    assert plan.queries[0].startswith("site:dom.mingkh.ru")
    assert '"Пермь" местные новости' in plan.queries


@pytest.mark.asyncio
async def test_extracts_only_explicit_labeled_residential_facts():
    adapter = MingkhResidentialAdapter(_Web(), _Snapshots())
    html = """
    <html><body>
      <h1>Пермь, Комсомольский проспект, 27</h1>
      <dl>
        <dt>Количество квартир</dt><dd>32</dd>
        <dt>Жилых помещений</dt><dd>32</dd>
        <dt>Количество жителей</dt><dd>81 чел.</dd>
      </dl>
    </body></html>
    """
    result = await adapter.extract(
        _task(),
        _fetched(html),
        _request("residential_population", "residential_premises_count"),
    )

    assert not result.blocked
    assert not result.errors
    facts = {item.data["intent"]: item for item in result.observations}
    assert facts["residential_premises_count"].data["value"] == 32
    assert facts["residential_population"].data["value"] == 81
    assert all(item.data["estimated"] is False for item in facts.values())
    assert {item.text for item in result.evidence} == {
        "Количество квартир: 32",
        "Количество жителей: 81",
    }


@pytest.mark.asyncio
async def test_population_is_not_estimated_from_apartment_count():
    adapter = MingkhResidentialAdapter(_Web(), _Snapshots())
    html = """
    <html><body>
      <h1>Пермь, Комсомольский проспект, 27</h1>
      <div>Количество квартир</div><div>32</div>
    </body></html>
    """
    result = await adapter.extract(
        _task(),
        _fetched(html),
        _request("residential_population", "residential_premises_count"),
    )

    facts = {item.data["intent"]: item.data["value"] for item in result.observations}
    assert facts == {"residential_premises_count": 32}
    assert "residential_population" not in facts


@pytest.mark.asyncio
async def test_russian_access_challenge_is_blocked_not_solved():
    adapter = MingkhResidentialAdapter(_Web(), _Snapshots())
    html = """
    <html><body>
      <h1>Хотим убедиться, что вы не робот</h1>
      <p>Пожалуйста, решите пример 6 + 7</p>
    </body></html>
    """
    result = await adapter.extract(
        _task(),
        _fetched(html),
        _request("residential_population", "residential_premises_count"),
    )

    assert result.blocked is True
    assert result.observations == []
    assert result.evidence == []
    assert [error.code for error in result.errors] == ["MINGKH_ACCESS_CHALLENGE"]


@pytest.mark.asyncio
async def test_wrong_house_never_becomes_residential_evidence():
    adapter = MingkhResidentialAdapter(_Web(), _Snapshots())
    html = """
    <html><body>
      <h1>Пермь, Комсомольский проспект, 29</h1>
      <div>Количество квартир: 99</div>
    </body></html>
    """
    result = await adapter.extract(
        _task(),
        _fetched(html),
        _request("residential_premises_count"),
    )

    assert result.observations == []
    assert result.evidence == []
    assert [error.code for error in result.errors] == ["MINGKH_TERRITORY_MISMATCH"]
