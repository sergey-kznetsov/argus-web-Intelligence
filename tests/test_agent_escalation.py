from __future__ import annotations

from types import SimpleNamespace

import pytest

from argus.crawler.models import FetchResult
from argus.sources.recipe_web import LifecycleRecipeWebAdapter


def fetched(*, text: str, runtime: str = "browser", blocked: bool = False) -> FetchResult:
    return FetchResult(
        url="https://example.com",
        final_url="https://example.com",
        status_code=200,
        content_type="text/html",
        text=text,
        blocked=blocked,
        runtime=runtime,
    )


@pytest.mark.asyncio
async def test_agent_receives_rendered_browser_context_only_when_content_is_insufficient() -> None:
    browser_result = fetched(text="")
    agent_result = fetched(text="verified rendered result", runtime="agent-replay")
    contexts: list[FetchResult | None] = []

    class Browser:
        async def fetch(self, url: str) -> FetchResult:
            assert url == "https://example.com"
            return browser_result

    adapter = object.__new__(LifecycleRecipeWebAdapter)
    adapter.browser = Browser()
    adapter.agent = SimpleNamespace(name="auto")
    adapter._needs_browser = lambda text: not text.strip()

    async def guided(task, *, context_fetch=None):
        del task
        contexts.append(context_fetch)
        return agent_result

    adapter._agent_guided_fetch = guided
    result = await adapter._browser_or_agent(SimpleNamespace(url="https://example.com"))

    assert result is agent_result
    assert contexts == [browser_result]


@pytest.mark.asyncio
async def test_browser_access_challenge_never_escalates_to_agent_bypass() -> None:
    browser_result = fetched(text="verify you are human", blocked=True)

    class Browser:
        async def fetch(self, url: str) -> FetchResult:
            del url
            return browser_result

    adapter = object.__new__(LifecycleRecipeWebAdapter)
    adapter.browser = Browser()
    adapter.agent = SimpleNamespace(name="auto")
    adapter._needs_browser = lambda text: True

    async def forbidden(*args, **kwargs):
        del args, kwargs
        raise AssertionError("blocked browser response must stop escalation")

    adapter._agent_guided_fetch = forbidden
    result = await adapter._browser_or_agent(SimpleNamespace(url="https://example.com"))

    assert result is browser_result
