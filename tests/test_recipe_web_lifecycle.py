from __future__ import annotations

from pathlib import Path

import pytest

from argus.crawler.agent.base import AgentResult
from argus.crawler.models import FetchResult
from argus.extraction.pdf import BoundedPdfExtractor
from argus.history.snapshots import SnapshotService
from argus.recipes.models import RecipeStep, SiteRecipe
from argus.recipes.service import RecipeManager
from argus.sources.base import SourceTask
from argus.sources.recipe_web import LifecycleRecipeWebAdapter
from argus.storage.lifecycle_sqlite import LifecycleAtomicSQLiteRepository


class FastSuccess:
    def __init__(self) -> None:
        self.calls = 0

    async def fetch(self, url: str) -> FetchResult:
        self.calls += 1
        return FetchResult(
            url=url,
            final_url=url,
            status_code=200,
            content_type="text/html",
            text="<html><body>fast fallback</body></html>",
            runtime="fast",
        )


class BrowserAlwaysFails:
    def __init__(self) -> None:
        self.calls = 0

    async def fetch(self, url: str, recipe=None) -> FetchResult:
        del url, recipe
        self.calls += 1
        raise RuntimeError("selector no longer matches")


class BrowserBlockedCandidate:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def fetch(self, url: str, recipe=None) -> FetchResult:
        self.calls.append((url, recipe))
        return FetchResult(
            url=url,
            final_url=url,
            status_code=403,
            content_type="text/html",
            text="access denied",
            blocked=True,
            runtime="browser_recipe" if recipe is not None else "browser",
        )


class BrowserSuccessfulCandidate:
    async def fetch(self, url: str, recipe=None) -> FetchResult:
        return FetchResult(
            url=url,
            final_url=url,
            status_code=200,
            content_type="text/html",
            text="<html><body>verified result</body></html>",
            runtime="browser_recipe" if recipe is not None else "browser",
            metadata={},
        )


class AgentWithRecipe:
    name = "test-agent"

    def __init__(self, visited_urls: list[str] | None = None) -> None:
        self.visited_urls = visited_urls or []

    async def run(self, task) -> AgentResult:
        del task
        return AgentResult(
            success=True,
            data={},
            visited_urls=list(self.visited_urls),
            actions=[{"scroll": {"pixels": 500}}],
        )


def source_task() -> SourceTask:
    return SourceTask(
        source_id="generic_web",
        goal="reviews",
        url="https://example.com/reviews",
        metadata={"collection_id": "recipe-web-lifecycle"},
    )


def build_adapter(
    repository: LifecycleAtomicSQLiteRepository,
    *,
    fast,
    browser,
    agent=None,
    failure_threshold: int = 3,
) -> LifecycleRecipeWebAdapter:
    manager = RecipeManager(
        repository,
        failure_threshold=failure_threshold,
        max_age_days=30,
        keep_versions=10,
    )
    return LifecycleRecipeWebAdapter(
        repository=repository,
        fast=fast,
        browser=browser,
        snapshots=SnapshotService(repository),
        recipes=manager,
        agent=agent,
        pdf_extractor=BoundedPdfExtractor(
            max_bytes=1_000_000,
            max_pages=10,
            max_text_chars=100_000,
            timeout_seconds=1.0,
            memory_mb=128,
        ),
    )


@pytest.mark.asyncio
async def test_repeated_recipe_replay_failures_invalidate_and_stop_future_replay(tmp_path: Path):
    repository = LifecycleAtomicSQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    fast = FastSuccess()
    browser = BrowserAlwaysFails()
    web = build_adapter(repository, fast=fast, browser=browser, failure_threshold=3)
    recipe = SiteRecipe(
        domain="example.com",
        goal="reviews",
        steps=[RecipeStep(action="click", selector="#reviews")],
    )
    await web.recipes.save(recipe)

    for _ in range(3):
        result = await web.fetch(source_task())
        assert result.runtime == "fast"

    stored = await repository.get_recipe("example.com", "reviews")
    assert stored is not None
    assert stored.status == "invalidated"
    assert stored.failures == 3
    assert stored.total_failures == 3
    assert browser.calls == 3

    # Invalidated latest version is no longer replayed; FAST is used directly.
    result = await web.fetch(source_task())
    assert result.runtime == "fast"
    assert browser.calls == 3
    assert fast.calls == 4
    await repository.close()


@pytest.mark.asyncio
async def test_blocked_candidate_verification_stops_without_alternate_url_bypass(tmp_path: Path):
    repository = LifecycleAtomicSQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    browser = BrowserBlockedCandidate()
    web = build_adapter(
        repository,
        fast=FastSuccess(),
        browser=browser,
        agent=AgentWithRecipe(
            visited_urls=[
                "https://example.com/reviews",
                "https://example.com/alternate",
            ]
        ),
    )

    result = await web._agent_guided_fetch(source_task())

    assert result is not None
    assert result.blocked is True
    assert len(browser.calls) == 1
    assert browser.calls[0][0] == "https://example.com/reviews"
    assert result.metadata["agent_compiled_recipe"] is False
    rejected = result.metadata["recipe_candidate_rejected"]
    assert isinstance(rejected, dict)
    assert rejected["status"] == "invalidated"
    assert rejected["invalidation_reason"] == "verification_blocked"
    assert await repository.get_recipe("example.com", "reviews") is None
    await repository.close()


@pytest.mark.asyncio
async def test_successful_candidate_replay_promotes_verified_recipe(tmp_path: Path):
    repository = LifecycleAtomicSQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    web = build_adapter(
        repository,
        fast=FastSuccess(),
        browser=BrowserSuccessfulCandidate(),
        agent=AgentWithRecipe(),
    )

    result = await web._agent_guided_fetch(source_task())

    assert result is not None
    assert result.blocked is False
    assert result.metadata["agent_compiled_recipe"] is True
    lifecycle = result.metadata["recipe_lifecycle"]
    assert isinstance(lifecycle, dict)
    assert lifecycle["status"] == "active"
    assert lifecycle["verified"] is True
    stored = await repository.get_recipe("example.com", "reviews")
    assert stored is not None
    assert stored.status == "active"
    assert stored.verified_at is not None
    assert stored.successes == 1
    await repository.close()
