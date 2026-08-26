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


class FastUnused:
    async def fetch(self, url: str) -> FetchResult:
        raise AssertionError(f"FAST must not run in extension test: {url}")


class BrowserRecordingRecipe:
    def __init__(self) -> None:
        self.recipes = []

    async def fetch(self, url: str, recipe=None) -> FetchResult:
        self.recipes.append(recipe)
        metadata = {}
        if recipe is not None:
            metadata = {
                "recipe_id": recipe.recipe_id,
                "recipe_version": recipe.version,
            }
        return FetchResult(
            url=url,
            final_url=url,
            status_code=200,
            content_type="text/html",
            text="<html><body>more reviews revealed</body></html>",
            runtime="browser_recipe" if recipe is not None else "browser",
            metadata=metadata,
        )


class MoreReviewsAgent:
    name = "test-agent"

    async def run(self, task) -> AgentResult:
        del task
        return AgentResult(
            success=True,
            data={},
            visited_urls=[],
            actions=[{"click": {"selector": "#more-reviews"}}],
            metadata={"status": "success", "code": "AGENT_OK"},
        )


def source_task() -> SourceTask:
    return SourceTask(
        source_id="generic_web",
        goal="reviews",
        url="https://example.com/place",
        metadata={
            "collection_id": "recipe-extension",
            "research_goals": ["reviews"],
        },
    )


def build_adapter(repository, browser) -> LifecycleRecipeWebAdapter:
    return LifecycleRecipeWebAdapter(
        repository=repository,
        fast=FastUnused(),
        browser=browser,
        snapshots=SnapshotService(repository),
        recipes=RecipeManager(repository),
        agent=MoreReviewsAgent(),
        pdf_extractor=BoundedPdfExtractor(
            max_bytes=1_000_000,
            max_pages=10,
            max_text_chars=100_000,
            timeout_seconds=1.0,
            memory_mb=128,
        ),
    )


@pytest.mark.asyncio
async def test_agent_extends_only_recipe_that_produced_context_dom(tmp_path: Path):
    repository = LifecycleAtomicSQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    browser = BrowserRecordingRecipe()
    web = build_adapter(repository, browser)
    base = SiteRecipe(
        domain="example.com",
        goal="reviews",
        steps=[RecipeStep(action="click", selector="#reviews")],
    )
    await web.recipes.save(base)
    context = FetchResult(
        url="https://example.com/place",
        final_url="https://example.com/place",
        status_code=200,
        content_type="text/html",
        text="<html><body><button id='more-reviews'>More</button></body></html>",
        runtime="browser_recipe",
        metadata={"recipe_id": base.recipe_id, "recipe_version": base.version},
    )
    task = source_task()

    result = await web._agent_guided_fetch(task, context_fetch=context)

    assert result is not None
    assert len(browser.recipes) == 1
    candidate = browser.recipes[0]
    assert candidate is not None
    assert [step.action for step in candidate.steps] == ["click", "click"]
    assert [step.selector for step in candidate.steps] == ["#reviews", "#more-reviews"]
    extension = task.metadata["agent_recipe_extension"]
    assert extension["base_recipe_id"] == base.recipe_id
    assert extension["accepted"] is True

    # The extension is technically replayable but remains ephemeral until extraction
    # proves the research goal with source-backed Evidence.
    stored = await repository.get_recipe("example.com", "reviews")
    assert stored is not None
    assert stored.recipe_id == base.recipe_id
    assert stored.version == 1
    pending = task.metadata["pending_recipe_candidate_ids"]
    assert isinstance(pending, list)
    assert candidate.recipe_id in pending
    lifecycle = result.metadata["recipe_lifecycle"]
    assert lifecycle["status"] == "candidate"
    await repository.close()


@pytest.mark.asyncio
async def test_agent_does_not_extend_unrelated_recipe_context(tmp_path: Path):
    repository = LifecycleAtomicSQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    browser = BrowserRecordingRecipe()
    web = build_adapter(repository, browser)
    base = SiteRecipe(
        domain="example.com",
        goal="reviews",
        steps=[RecipeStep(action="click", selector="#reviews")],
    )
    await web.recipes.save(base)
    context = FetchResult(
        url="https://example.com/place",
        final_url="https://example.com/place",
        status_code=200,
        content_type="text/html",
        text="<html><body><button id='more-reviews'>More</button></body></html>",
        runtime="browser_recipe",
        metadata={"recipe_id": "different-recipe"},
    )

    result = await web._agent_guided_fetch(source_task(), context_fetch=context)

    assert result is not None
    candidate = browser.recipes[0]
    assert candidate is not None
    assert len(candidate.steps) == 1
    assert candidate.steps[0].selector == "#more-reviews"
    stored = await repository.get_recipe("example.com", "reviews")
    assert stored is not None
    assert stored.recipe_id == base.recipe_id
    await repository.close()
