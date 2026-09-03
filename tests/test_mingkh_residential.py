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
    def __init__(self, guided: FetchResult | None = None) -> None:
        self.guided = guided
        self.navigation_calls = 0
        self.finalized: list[tuple[SourceTask, object]] = []

    async def navigate_with_agent(self, task, *, context_fetch):
        del context_fetch
        self.navigation_calls += 1
        return self.guided

    async def finalize_navigation_goal(self, task, request, result):
        del request
        self.finalized.append((task, result))

    async def health(self):
        return {"status": "ok"}


def _request(*intents: str, address: str = "Комсомольский проспект, 27") -> CollectionRequest:
    return CollectionRequest(
        consumer="consumer-neutral-test",
        analysis_id="analysis-1",
        territory={"city": "Пермь", "address": address},
        intents=list(intents),
    )


def _task(
    *,
    goal: str = "residential_premises_count",
    url: str = "https://dom.mingkh.ru/perm/perm/123456",
) -> SourceTask:
    return SourceTask(
        source_id="mingkh_residential",
        goal=goal,
        url=url,
        metadata={"collection_id": "collection-1"},
    )


def _fetched(
    html: str,
    *,
    blocked: bool = False,
    url: str = "https://dom.mingkh.ru/perm/perm/123456",
    links: list[str] | None = None,
    metadata: dict[str, object] | None = None,
) -> FetchResult:
    return FetchResult(
        url=url,
        final_url=url,
        status_code=200,
        content_type="text/html; charset=utf-8",
        text=html,
        title="Дом",
        links=list(links or []),
        blocked=blocked,
        runtime="browser",
        metadata=dict(metadata or {}),
    )


@pytest.mark.asyncio
async def test_residential_only_plan_uses_direct_mandatory_source_before_search_fallback():
    planner = CuratedResidentialResearchPlanner(_FailPlanner(), max_queries=8)
    plan = await planner.plan(
        _request("residential_population", "residential_premises_count")
    )

    assert plan.queries == []
    assert len(plan.tasks) == 1
    assert plan.tasks[0].source_id == "site_discovery"
    assert plan.tasks[0].metadata["site_discovery_target_source_id"] == "mingkh_residential"
    assert plan.tasks[0].metadata["source_owned_navigation"] is True
    assert any("fallback_sources=false" in note for note in plan.notes)
    assert any("search_provider=followup_only" in note for note in plan.notes)


@pytest.mark.asyncio
async def test_mixed_plan_hides_residential_intents_and_search_queries_from_general_planner():
    delegate = _RecordingPlanner()
    planner = CuratedResidentialResearchPlanner(delegate, max_queries=8)
    plan = await planner.plan(_request("residential_population", "local_news"))

    assert delegate.intents == ["local_news"]
    assert plan.queries == ['"Пермь" новости']
    assert not any(query.startswith("site:dom.mingkh.ru") for query in plan.queries)
    assert plan.tasks[0].metadata["site_discovery_target_source_id"] == "mingkh_residential"


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
    assert any("search_provider=fallback_navigation" in note for note in plan.notes)


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
    web = _Web()
    adapter = MingkhResidentialAdapter(web, _Snapshots())
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
    assert web.navigation_calls == 0
    assert web.finalized[-1][1] is result


@pytest.mark.asyncio
async def test_population_is_not_estimated_from_apartment_count():
    web = _Web()
    adapter = MingkhResidentialAdapter(web, _Snapshots())
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
    assert web.navigation_calls == 0


@pytest.mark.asyncio
async def test_accessible_interface_can_reveal_fact_through_one_verified_navigation_round():
    target_url = "https://dom.mingkh.ru/perm/perm/123456"
    guided = _fetched(
        """
        <html><body>
          <h1>Пермь, Комсомольский проспект, 27</h1>
          <div>Количество жителей: 81</div>
        </body></html>
        """,
        url=target_url,
        metadata={
            "agent_backend": "test-agent",
            "recipe_id": "recipe-candidate-1",
            "recipe_version": 2,
        },
    )
    web = _Web(guided)
    adapter = MingkhResidentialAdapter(web, _Snapshots())
    task = _task(
        goal="residential_population",
        url="https://dom.mingkh.ru/search",
    )
    initial = _fetched(
        """
        <html><body>
          <h1>Поиск дома</h1>
          <form method="get"><label>Адрес<input name="address"></label></form>
        </body></html>
        """,
        url=task.url,
    )

    result = await adapter.extract(
        task,
        initial,
        _request("residential_population"),
    )

    assert web.navigation_calls == 1
    assert len(result.observations) == 1
    fact = result.observations[0]
    assert fact.data["intent"] == "residential_population"
    assert fact.data["value"] == 81
    assert fact.provenance["recipe_id"] == "recipe-candidate-1"
    interface = fact.provenance["interface_navigation"]
    assert interface["verified_browser_replay"] is True
    assert interface["agent_output_is_evidence"] is False
    assert task.metadata["mingkh_interface_navigation_result"] == "source_goal_revealed"
    assert "Пермь, Комсомольский проспект, 27" in task.metadata[
        "research_input_candidates"
    ]
    assert web.finalized[-1][1] is result


@pytest.mark.asyncio
async def test_same_domain_house_links_are_preferred_before_agent_navigation():
    web = _Web()
    adapter = MingkhResidentialAdapter(web, _Snapshots())
    task = _task(goal="residential_population", url="https://dom.mingkh.ru/search")
    initial = _fetched(
        "<html><body><h1>Результаты поиска</h1></body></html>",
        url=task.url,
        links=["https://dom.mingkh.ru/perm/perm/123456"],
    )

    result = await adapter.extract(task, initial, _request("residential_population"))

    assert web.navigation_calls == 0
    assert [item.url for item in result.discovered_tasks] == [
        "https://dom.mingkh.ru/perm/perm/123456"
    ]


@pytest.mark.asyncio
async def test_russian_access_challenge_is_blocked_not_solved():
    web = _Web()
    adapter = MingkhResidentialAdapter(web, _Snapshots())
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
    assert web.navigation_calls == 0


@pytest.mark.asyncio
async def test_wrong_house_never_becomes_residential_evidence_or_agent_navigation():
    web = _Web()
    adapter = MingkhResidentialAdapter(web, _Snapshots())
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
    assert web.navigation_calls == 0
