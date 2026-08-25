from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

import pytest

from argus.config import Settings
from argus.crawler.agent.base import AgentTask
from argus.crawler.agent.browser_use import BrowserUseAgent
from argus.security.urls import UrlGuard


class FakeHistory:
    def __init__(self, *, success, final="", urls=None, actions=None, errors=None) -> None:
        self._success = success
        self._final = final
        self._urls = urls or []
        self._actions = actions or []
        self._errors = errors or []

    def final_result(self):
        return self._final

    def is_successful(self):
        return self._success

    def urls(self):
        return list(self._urls)

    def model_actions(self):
        return list(self._actions)

    def errors(self):
        return list(self._errors)


class FakeAgent:
    history = FakeHistory(success=True, final="ok", urls=["http://localhost"])
    delay = 0.0

    def __init__(self, *, task, llm, browser) -> None:
        del task, llm, browser

    async def run(self, max_steps):
        assert max_steps > 0
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.history


class FakeBrowser:
    instances: list["FakeBrowser"] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.closed = False
        self.__class__.instances.append(self)

    async def close(self) -> None:
        self.closed = True


class FakeChatOllama:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs


def install_browser_use(monkeypatch) -> None:
    module = SimpleNamespace(Agent=FakeAgent, Browser=FakeBrowser, ChatOllama=FakeChatOllama)
    monkeypatch.setitem(sys.modules, "browser_use", module)
    FakeBrowser.instances.clear()
    FakeAgent.delay = 0.0
    FakeAgent.history = FakeHistory(
        success=True,
        final="ok",
        urls=["http://localhost/source"],
    )


def build_agent(tmp_path) -> BrowserUseAgent:
    settings = Settings(
        token_file=tmp_path / "token",
        browser_timeout_seconds=1.0,
        fetch_wait_timeout_seconds=2.0,
    )
    return BrowserUseAgent(settings, UrlGuard.from_strings(["localhost"]))


def task(**context) -> AgentTask:
    return AgentTask(
        url="http://localhost/source",
        goal="public_mentions",
        instruction="Find the public source",
        context=context,
    )


@pytest.mark.asyncio
async def test_agent_timeout_is_bounded_and_browser_is_closed(tmp_path, monkeypatch):
    install_browser_use(monkeypatch)
    agent = build_agent(tmp_path)
    agent.timeout_seconds = 0.01
    FakeAgent.delay = 0.05

    result = await agent.run(task())

    assert result.success is False
    assert result.blocked is False
    assert result.metadata["reason_code"] == "AGENT_TIMEOUT"
    assert FakeBrowser.instances and FakeBrowser.instances[0].closed is True


@pytest.mark.asyncio
async def test_agent_rejects_action_count_above_replay_budget(tmp_path, monkeypatch):
    install_browser_use(monkeypatch)
    agent = build_agent(tmp_path)
    FakeAgent.history = FakeHistory(
        success=True,
        final="ok",
        urls=["http://localhost/source"],
        actions=[{"scroll": {"pixels": 100}} for _ in range(agent.max_actions + 1)],
    )

    result = await agent.run(task())

    assert result.success is False
    assert result.actions == []
    assert result.metadata["reason_code"] == "AGENT_ACTION_BUDGET_EXCEEDED"


@pytest.mark.asyncio
async def test_agent_rejects_deep_action_payload(tmp_path, monkeypatch):
    install_browser_use(monkeypatch)
    agent = build_agent(tmp_path)
    payload = {"value": "x"}
    for _ in range(agent.max_action_depth + 2):
        payload = {"nested": payload}
    FakeAgent.history = FakeHistory(
        success=True,
        final="ok",
        urls=["http://localhost/source"],
        actions=[{"click": payload}],
    )

    result = await agent.run(task())

    assert result.success is False
    assert result.metadata["reason_code"] == "AGENT_ACTION_PAYLOAD_BUDGET_EXCEEDED"


@pytest.mark.asyncio
async def test_agent_marks_access_challenge_blocked(tmp_path, monkeypatch):
    install_browser_use(monkeypatch)
    agent = build_agent(tmp_path)
    FakeAgent.history = FakeHistory(
        success=False,
        final="Verify you are human - CAPTCHA",
        urls=["http://localhost/source"],
    )

    result = await agent.run(task())

    assert result.success is False
    assert result.blocked is True
    assert result.metadata["reason_code"] == "AGENT_ACCESS_CHALLENGE"


@pytest.mark.asyncio
async def test_agent_refuses_target_outside_allowed_domain_boundary(tmp_path, monkeypatch):
    install_browser_use(monkeypatch)
    agent = build_agent(tmp_path)

    result = await agent.run(task(allowed_domains=["example.com"]))

    assert result.success is False
    assert result.metadata["reason_code"] == "AGENT_TARGET_OUTSIDE_ALLOWED_DOMAINS"
    assert FakeBrowser.instances == []


@pytest.mark.asyncio
async def test_agent_bounds_and_deduplicates_visited_urls(tmp_path, monkeypatch):
    install_browser_use(monkeypatch)
    agent = build_agent(tmp_path)
    urls = ["http://localhost/source", "http://localhost/source"] + [
        f"http://localhost/page-{index}" for index in range(agent.max_visited_urls + 5)
    ]
    FakeAgent.history = FakeHistory(success=True, final="ok", urls=urls)

    result = await agent.run(task())

    assert result.success is True
    assert len(result.visited_urls) <= agent.max_visited_urls
    assert len(result.visited_urls) == len(set(result.visited_urls))
    assert result.metadata["visited_urls_truncated"] is True
