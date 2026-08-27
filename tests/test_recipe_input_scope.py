from __future__ import annotations

from pathlib import Path

import pytest

from argus.crawler.models import FetchResult
from argus.extraction.pdf import BoundedPdfExtractor
from argus.history.snapshots import SnapshotService
from argus.recipes.models import RecipeStep, SiteRecipe
from argus.recipes.service import RecipeManager
from argus.sources.base import SourceTask
from argus.sources.recipe_web import LifecycleRecipeWebAdapter
from argus.storage.lifecycle_sqlite import LifecycleAtomicSQLiteRepository


class _FastSuccess:
    def __init__(self) -> None:
        self.calls = 0

    async def fetch(self, url: str) -> FetchResult:
        self.calls += 1
        return FetchResult(
            url=url,
            final_url=url,
            status_code=200,
            content_type="text/html",
            text="<html><body>fresh request page</body></html>",
            runtime="fast",
        )


class _BrowserMustNotReplay:
    def __init__(self) -> None:
        self.calls = 0

    async def fetch(self, url: str, recipe=None) -> FetchResult:
        del url, recipe
        self.calls += 1
        raise AssertionError("literal fill recipe leaked into a different research input scope")


def _adapter(repository, fast, browser) -> LifecycleRecipeWebAdapter:
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
async def test_active_fill_recipe_is_skipped_for_different_research_input(tmp_path: Path):
    repository = LifecycleAtomicSQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    fast = _FastSuccess()
    browser = _BrowserMustNotReplay()
    web = _adapter(repository, fast, browser)
    recipe = SiteRecipe(
        domain="example.com",
        goal="residential_population",
        steps=[
            RecipeStep(
                action="fill",
                selector="input[name='address']",
                value="Пермь, Комсомольский проспект, 27",
            ),
            RecipeStep(action="press", selector="input[name='address']", value="Enter"),
        ],
    )
    await web.recipes.save(recipe)
    task = SourceTask(
        source_id="generic_web",
        goal="residential_population",
        url="https://example.com/search",
        metadata={
            "research_input_candidates": ["Пермь, улица Ленина, 10"],
        },
    )

    result = await web.fetch(task)

    assert result.runtime == "fast"
    assert browser.calls == 0
    assert fast.calls == 1
    assert task.metadata["active_recipe_replay_suppressed"] == (
        "research_input_scope_mismatch"
    )
    assert task.metadata["active_recipe_replay_suppressed_recipe_id"] == recipe.recipe_id

    stored = await repository.get_recipe("example.com", "residential_population")
    assert stored is not None
    assert stored.status == "active"
    assert stored.failures == 0
    assert stored.total_failures == 0
    await repository.close()


def test_fill_recipe_requires_current_allowed_input_but_static_recipe_does_not():
    fill_recipe = SiteRecipe(
        domain="example.com",
        goal="lookup",
        steps=[RecipeStep(action="fill", selector="#q", value="Пермь, дом 27")],
    )
    static_recipe = SiteRecipe(
        domain="example.com",
        goal="lookup",
        steps=[RecipeStep(action="click", selector="#details")],
    )
    matching = SourceTask(
        source_id="generic_web",
        goal="lookup",
        url="https://example.com",
        metadata={"research_input_candidates": ["Пермь, дом 27"]},
    )
    different = SourceTask(
        source_id="generic_web",
        goal="lookup",
        url="https://example.com",
        metadata={"research_input_candidates": ["Пермь, дом 29"]},
    )

    assert LifecycleRecipeWebAdapter._recipe_replay_compatible(matching, fill_recipe)
    assert not LifecycleRecipeWebAdapter._recipe_replay_compatible(different, fill_recipe)
    assert LifecycleRecipeWebAdapter._recipe_replay_compatible(different, static_recipe)
