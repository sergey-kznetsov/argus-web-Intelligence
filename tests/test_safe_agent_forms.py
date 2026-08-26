from __future__ import annotations

import pytest

from argus.config import Settings
from argus.contracts.models import CollectionRequest
from argus.crawler.agent.ollama_recipe import OllamaRecipeAgent
from argus.recipes.compiler import AgentRecipeCompiler
from argus.research.discovery import DiscoveryHit
from argus.research.planner import ResearchPlan
from argus.research.task_context import ResearchInputDiscoveryService, ResearchInputPlanner
from argus.sources.base import SourceTask


class AllowGuard:
    async def validate(self, url: str) -> str:
        return url


class FakePlanner:
    async def plan(self, request: CollectionRequest) -> ResearchPlan:
        del request
        return ResearchPlan(
            queries=["архив Пермь Комсомольский проспект 27"],
            tasks=[
                SourceTask(
                    source_id="generic_web",
                    goal="historical_context",
                    url="https://example.com/archive",
                )
            ],
        )


class FakeProvider:
    name = "fake-search"

    async def discover(self, queries: list[str], request: CollectionRequest) -> list[DiscoveryHit]:
        del request
        return [
            DiscoveryHit(
                url="https://example.com/card",
                provider=self.name,
                query=queries[0],
            )
        ]

    async def health(self) -> dict[str, object]:
        return {"status": "ok"}


def request() -> CollectionRequest:
    return CollectionRequest(
        consumer="safe-form-test",
        analysis_id="safe-form-test",
        territory={"city": "Пермь", "address": "Комсомольский проспект, 27"},
        intents=["historical_context"],
        constraints={"max_pages": 10},
    )


def build_agent() -> OllamaRecipeAgent:
    return OllamaRecipeAgent(
        Settings(browser_serp_enabled=False, searxng_url=None),
        AllowGuard(),
    )


@pytest.mark.asyncio
async def test_safe_get_search_form_exposes_only_preapproved_input_values():
    agent = build_agent()
    candidate = "Пермь, Комсомольский проспект, 27"
    controls = await agent._controls(
        """
        <form method="get" action="/search">
          <input type="search" name="q" aria-label="Поиск по адресу">
          <select name="year" aria-label="Год">
            <option value="">Все годы</option>
            <option value="1917">1917</option>
            <option value="1940">1940</option>
          </select>
        </form>
        """,
        page_url="https://example.com/archive",
        allowed_domains=["example.com"],
        input_candidates=[candidate],
    )

    fills = [item for item in controls if item.kind == "fill"]
    presses = [item for item in controls if item.kind == "press"]
    selects = [item for item in controls if item.kind == "select"]

    assert [(item.selector, item.value) for item in fills] == [
        ('input[name="q"]', candidate)
    ]
    assert [(item.selector, item.value) for item in presses] == [
        ('input[name="q"]', "Enter")
    ]
    assert {item.value for item in selects} == {"1917", "1940"}

    fill = fills[0]
    actions = agent._actions_from_plan(
        {"actions": [{"control_id": fill.control_id, "value": "ПОДМЕНА"}]},
        controls,
    )
    assert actions == [
        {"fill": {"selector": 'input[name="q"]', "value": candidate}}
    ]


@pytest.mark.asyncio
async def test_post_and_sensitive_forms_are_not_exposed_to_agent():
    agent = build_agent()
    controls = await agent._controls(
        """
        <form method="post" action="/search">
          <input type="search" name="q" aria-label="Поиск по адресу">
        </form>
        <form method="get" action="/login">
          <input type="password" name="password" aria-label="Пароль">
        </form>
        <input type="file" name="upload" aria-label="Загрузить файл">
        """,
        page_url="https://example.com/archive",
        allowed_domains=["example.com"],
        input_candidates=["Пермь, Комсомольский проспект, 27"],
    )

    assert not [item for item in controls if item.kind in {"fill", "press", "select"}]


def test_recipe_compiler_preserves_prevalidated_select_value():
    steps = AgentRecipeCompiler().compile(
        [{"select": {"selector": 'select[name="year"]', "value": "1940"}}]
    )

    assert steps is not None
    assert len(steps) == 1
    assert steps[0].action == "select"
    assert steps[0].selector == 'select[name="year"]'
    assert steps[0].value == "1940"


@pytest.mark.asyncio
async def test_planner_and_discovery_tasks_receive_same_bounded_navigation_context():
    collection = request()
    planner = ResearchInputPlanner(FakePlanner())
    plan = await planner.plan(collection)
    planned = plan.tasks[0]

    discovery = ResearchInputDiscoveryService(
        providers=[FakeProvider()],
        url_guard=AllowGuard(),
        max_queries=8,
    )
    outcome = await discovery.discover(plan.queries, collection)
    discovered = outcome.tasks[0]

    for task in (planned, discovered):
        values = task.metadata["research_input_candidates"]
        assert "архив Пермь Комсомольский проспект 27" in values
        assert "Пермь, Комсомольский проспект, 27" in values
        assert task.metadata["research_input_candidates_navigation_only"] is True
        assert task.metadata["research_input_candidates_are_evidence"] is False
