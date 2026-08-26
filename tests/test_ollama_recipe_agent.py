from __future__ import annotations

import pytest

from argus.config import Settings
from argus.crawler.agent.base import AgentTask
from argus.crawler.agent.ollama_recipe import OllamaRecipeAgent
from argus.recipes.compiler import AgentRecipeCompiler
from argus.security.urls import UnsafeUrlError


class GuardStub:
    async def validate(self, url: str) -> str:
        if "blocked.test" in url:
            raise UnsafeUrlError("blocked")
        return url


class FakeResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, str]:
        return {"response": '{"actions":[{"control_id":1},{"scroll_pixels":1200}]}' }


class FakeClient:
    def __init__(self, *args, **kwargs) -> None:
        del args, kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        del exc_type, exc, tb

    async def post(self, url: str, json: dict[str, object]):
        assert url.endswith("/api/generate")
        assert json["format"] == "json"
        return FakeResponse()


def build_agent() -> OllamaRecipeAgent:
    settings = Settings(
        ollama_url="http://127.0.0.1:11434",
        browser_serp_enabled=False,
        searxng_url=None,
    )
    return OllamaRecipeAgent(settings, GuardStub())


@pytest.mark.asyncio
async def test_native_agent_uses_only_safe_page_controls(monkeypatch):
    from argus.crawler.agent import ollama_recipe

    monkeypatch.setattr(ollama_recipe.httpx, "AsyncClient", FakeClient)
    agent = build_agent()
    html = """
    <html><body>
      <button data-testid="reviews">Отзывы</button>
      <button id="login">Войти</button>
      <a href="/comments">Комментарии</a>
      <a href="https://outside.test/reviews">External reviews</a>
      <a href="/download">Скачать</a>
    </body></html>
    """
    result = await agent.run(
        AgentTask(
            url="https://example.com/place",
            goal="reviews",
            instruction="Reveal public reviews",
            context={
                "page_html": html,
                "page_url": "https://example.com/place",
                "allowed_domains": ["example.com"],
            },
        )
    )

    assert result.success is True
    assert result.metadata["backend"] == "ollama-recipe"
    assert result.metadata["agent_output_is_evidence"] is False
    assert result.actions == [
        {"click": {"selector": 'button[data-testid="reviews"]'}},
        {"scroll": {"pixels": 1200}},
    ]


@pytest.mark.asyncio
async def test_controls_exclude_denied_cross_domain_and_state_changing_actions():
    agent = build_agent()
    controls = await agent._controls(
        """
        <button aria-label="Отзывы">Отзывы</button>
        <button aria-label="Купить">Купить</button>
        <form action="/feedback" method="post">
          <button type="submit" aria-label="Показать">Показать</button>
        </form>
        <button type="button" aria-label="Отправить">Отправить</button>
        <div role="button" data-testid="vote">Подробнее</div>
        <div role="tab" data-testid="reviews-tab">Отзывы посетителей</div>
        <a href="/details">Подробнее</a>
        <a href="https://outside.test/details">Подробнее снаружи</a>
        """,
        page_url="https://example.com/place",
        allowed_domains=["example.com"],
    )

    assert [item.label for item in controls] == [
        "Отзывы",
        "Подробнее",
        "Отзывы посетителей",
    ]
    assert controls[0].kind == "click"
    assert controls[1].url == "https://example.com/details"
    assert controls[2].selector == 'div[data-testid="reviews-tab"]'


@pytest.mark.asyncio
async def test_agent_requires_verified_page_context():
    agent = build_agent()
    result = await agent.run(
        AgentTask(
            url="https://example.com/place",
            goal="reviews",
            instruction="Reveal public reviews",
        )
    )

    assert result.success is False
    assert result.metadata["code"] == "AGENT_PAGE_CONTEXT_REQUIRED"


def test_agent_plan_cannot_invent_control_and_scroll_is_bounded():
    agent = build_agent()
    controls = [
        ollama_control(agent, 1, selector='button[data-testid="reviews"]'),
    ]

    actions = agent._actions_from_plan(
        {
            "actions": [
                {"control_id": 999},
                {"control_id": 1},
                {"control_id": 1},
                {"scroll_pixels": 999999},
            ]
        },
        controls,
    )

    assert actions == [
        {"click": {"selector": 'button[data-testid="reviews"]'}},
        {"scroll": {"pixels": 8000}},
    ]


def test_recipe_compiler_accepts_explicit_prevalidated_selector():
    steps = AgentRecipeCompiler().compile(
        [{"click": {"selector": 'button[data-testid="reviews"]'}}]
    )

    assert steps is not None
    assert len(steps) == 1
    assert steps[0].action == "click"
    assert steps[0].selector == 'button[data-testid="reviews"]'


def ollama_control(agent: OllamaRecipeAgent, control_id: int, *, selector: str):
    from argus.crawler.agent.ollama_recipe import _Control

    del agent
    return _Control(
        control_id=control_id,
        kind="click",
        label="Отзывы",
        selector=selector,
    )
