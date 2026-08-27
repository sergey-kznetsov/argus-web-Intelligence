from __future__ import annotations

from pathlib import Path

import pytest

from argus.contracts.models import CollectionRequest, CollectionStatus
from argus.crawler.errors import CrawlerRequestSkippedError
from argus.crawler.lifecycle import FetchBroker
from argus.extraction.pdf import BoundedPdfExtractor
from argus.history.snapshots import SnapshotService
from argus.orchestrator.atomic import AtomicCollectionOrchestrator
from argus.recipes.models import RecipeStep, SiteRecipe
from argus.recipes.service import RecipeManager
from argus.research.planner import ResearchPlan
from argus.sources.base import SourceTask
from argus.sources.recipe_web import LifecycleRecipeWebAdapter
from argus.sources.registry import SourceRegistry
from argus.storage.atomic_sqlite import AtomicSQLiteRepository
from argus.storage.lifecycle_sqlite import LifecycleAtomicSQLiteRepository


@pytest.mark.asyncio
async def test_fetch_broker_completes_all_waiters_when_crawlee_skips_same_url():
    broker = FetchBroker()
    key_a, future_a = broker.create("https://EXAMPLE.com:443/path?q=1#fragment")
    key_b, future_b = broker.create("https://example.com/path?q=1")

    matched = broker.reject_skipped("https://example.com/path?q=1", "robots_txt")

    assert matched == 2
    for future in (future_a, future_b):
        with pytest.raises(CrawlerRequestSkippedError) as raised:
            await future
        assert raised.value.robots_txt is True
        assert raised.value.reason == "robots_txt"

    broker.discard(key_a)
    broker.discard(key_b)
    assert broker.reject_skipped("https://example.com/path?q=1", "robots_txt") == 0


class _EmptyPlanner:
    async def plan(self, request):
        del request
        return ResearchPlan()


class _RobotsBlockedAdapter:
    source_id = "robots_test"
    intents = {"robots_test"}

    async def discover(self, request):
        del request
        return [
            SourceTask(
                source_id=self.source_id,
                goal="robots_test",
                url="https://example.com/disallowed",
            )
        ]

    async def fetch(self, task):
        raise CrawlerRequestSkippedError(task.url, "robots_txt")

    async def extract(self, task, fetched, request):
        raise AssertionError("a robots-skipped URL must never reach extraction")

    async def normalize(self, result):
        return result

    async def health(self):
        return {"status": "ok"}


@pytest.mark.asyncio
async def test_robots_skip_finishes_collection_as_blocked_without_timeout(tmp_path: Path):
    repository = AtomicSQLiteRepository(tmp_path / "robots.sqlite")
    registry = SourceRegistry()
    registry.register(_RobotsBlockedAdapter())
    orchestrator = AtomicCollectionOrchestrator(
        repository,
        registry,
        _EmptyPlanner(),
        auto_execute=False,
        source_task_timeout_seconds=5.0,
    )
    await orchestrator.start()
    accepted = await orchestrator.submit(
        CollectionRequest(
            consumer="robots-test",
            analysis_id="robots-test",
            territory={"city": "Пермь"},
            intents=["robots_test"],
            constraints={"max_pages": 2},
        )
    )

    await orchestrator.execute(accepted.collection_id)

    record = await repository.get_collection(accepted.collection_id)
    assert record is not None
    assert record.status == CollectionStatus.BLOCKED
    assert record.partial is False
    assert len(record.coverage) == 1
    assert record.coverage[0].blocked is True
    assert record.coverage[0].status == "blocked"
    assert record.coverage[0].error_code == "SOURCE_ROBOTS_TXT_BLOCKED"
    assert [error.code for error in record.errors] == ["SOURCE_ROBOTS_TXT_BLOCKED"]
    assert record.errors[0].retryable is False
    assert not any(error.code == "SOURCE_TASK_TIMEOUT" for error in record.errors)
    assert record.checkpoint["execution_budget"]["processed_pages"] == 1


class _FastRobots:
    def __init__(self) -> None:
        self.calls = 0

    async def fetch(self, url: str):
        self.calls += 1
        raise CrawlerRequestSkippedError(url, "robots_txt")


class _BrowserMustNotRun:
    def __init__(self) -> None:
        self.calls = 0

    async def fetch(self, url: str, recipe=None):
        del url, recipe
        self.calls += 1
        raise AssertionError("robots.txt must not be escalated to browser navigation")


def _web_adapter(repository, fast, browser) -> LifecycleRecipeWebAdapter:
    return LifecycleRecipeWebAdapter(
        repository=repository,
        fast=fast,
        browser=browser,
        snapshots=SnapshotService(repository),
        recipes=RecipeManager(
            repository,
            failure_threshold=3,
            max_age_days=30,
            keep_versions=10,
        ),
        pdf_extractor=BoundedPdfExtractor(
            max_bytes=1_000_000,
            max_pages=10,
            max_text_chars=100_000,
            timeout_seconds=1.0,
            memory_mb=128,
        ),
    )


@pytest.mark.asyncio
async def test_robots_skip_does_not_escalate_fast_to_browser_or_agent(tmp_path: Path):
    repository = LifecycleAtomicSQLiteRepository(tmp_path / "recipe.sqlite")
    await repository.initialize()
    fast = _FastRobots()
    browser = _BrowserMustNotRun()
    web = _web_adapter(repository, fast, browser)
    task = SourceTask(
        source_id="generic_web",
        goal="public_mentions",
        url="https://example.com/disallowed",
    )

    with pytest.raises(CrawlerRequestSkippedError) as raised:
        await web.fetch(task)

    assert raised.value.robots_txt is True
    assert fast.calls == 1
    assert browser.calls == 0
    await repository.close()


class _FastMustNotRun:
    async def fetch(self, url: str):
        raise AssertionError(f"active recipe should be attempted before FAST: {url}")


class _RecipeRobotsBrowser:
    def __init__(self) -> None:
        self.calls = 0

    async def fetch(self, url: str, recipe=None):
        assert recipe is not None
        self.calls += 1
        raise CrawlerRequestSkippedError(url, "robots_txt")


@pytest.mark.asyncio
async def test_robots_skip_does_not_penalize_active_site_recipe(tmp_path: Path):
    repository = LifecycleAtomicSQLiteRepository(tmp_path / "active-recipe.sqlite")
    await repository.initialize()
    browser = _RecipeRobotsBrowser()
    web = _web_adapter(repository, _FastMustNotRun(), browser)
    recipe = SiteRecipe(
        domain="example.com",
        goal="public_mentions",
        steps=[RecipeStep(action="click", selector="#details")],
    )
    await web.recipes.save(recipe)
    task = SourceTask(
        source_id="generic_web",
        goal="public_mentions",
        url="https://example.com/disallowed",
    )

    with pytest.raises(CrawlerRequestSkippedError):
        await web.fetch(task)

    stored = await repository.get_recipe("example.com", "public_mentions")
    assert stored is not None
    assert stored.recipe_id == recipe.recipe_id
    assert stored.failures == 0
    assert stored.total_failures == 0
    assert browser.calls == 1
    await repository.close()
