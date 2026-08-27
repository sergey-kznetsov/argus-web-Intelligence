from __future__ import annotations

import pytest

from argus.contracts.models import CollectionRequest
from argus.crawler.models import FetchResult
from argus.sources.base import SourceResult, SourceTask
from argus.sources.recipe_web import LifecycleRecipeWebAdapter


class _ProbeWebAdapter(LifecycleRecipeWebAdapter):
    def __init__(self) -> None:
        self.guided_calls = 0
        self.finalized: list[SourceResult] = []

    async def _agent_guided_fetch(self, task, *, context_fetch=None):
        del task
        self.guided_calls += 1
        assert context_fetch is not None
        return FetchResult(
            url=context_fetch.url,
            final_url="https://example.com/result",
            status_code=200,
            content_type="text/html",
            text="<html><body>verified replay</body></html>",
            runtime="browser_recipe",
        )

    async def _finalize_recipe_goal_verification(self, task, request, result):
        del task, request
        self.finalized.append(result)


def _task() -> SourceTask:
    return SourceTask(
        source_id="generic_web",
        goal="custom_fact",
        url="https://example.com/search",
    )


def _request() -> CollectionRequest:
    return CollectionRequest(
        consumer="contract-test",
        analysis_id="contract-test",
        territory={"city": "Пермь"},
        intents=["custom_fact"],
    )


def _context(*, blocked: bool = False) -> FetchResult:
    return FetchResult(
        url="https://example.com/search",
        final_url="https://example.com/search",
        status_code=403 if blocked else 200,
        content_type="text/html",
        text="<html><body>search</body></html>",
        blocked=blocked,
        runtime="browser",
    )


@pytest.mark.asyncio
async def test_public_guided_navigation_delegates_only_for_accessible_context():
    adapter = _ProbeWebAdapter()

    blocked = await adapter.navigate_with_agent(_task(), context_fetch=_context(blocked=True))
    assert blocked is None
    assert adapter.guided_calls == 0

    replayed = await adapter.navigate_with_agent(_task(), context_fetch=_context())
    assert replayed is not None
    assert replayed.runtime == "browser_recipe"
    assert adapter.guided_calls == 1


@pytest.mark.asyncio
async def test_public_navigation_finalization_delegates_to_goal_verification():
    adapter = _ProbeWebAdapter()
    result = SourceResult(observations=[], evidence=[])

    await adapter.finalize_navigation_goal(_task(), _request(), result)

    assert adapter.finalized == [result]
