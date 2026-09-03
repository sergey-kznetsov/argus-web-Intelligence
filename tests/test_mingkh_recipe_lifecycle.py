from __future__ import annotations

from pathlib import Path

import pytest

from argus.contracts.models import CollectionRequest
from argus.crawler.agent.base import AgentResult
from argus.crawler.models import FetchResult
from argus.extraction.pdf import BoundedPdfExtractor
from argus.history.snapshots import SnapshotService
from argus.recipes.service import RecipeManager
from argus.sources.base import SourceTask
from argus.sources.mingkh_residential import MingkhResidentialAdapter
from argus.sources.recipe_web import LifecycleRecipeWebAdapter
from argus.storage.lifecycle_sqlite import LifecycleAtomicSQLiteRepository


class _FastUnused:
    async def fetch(self, url: str) -> FetchResult:
        raise AssertionError(f"FAST should not be used by guided navigation test: {url}")


class _ResidentialRecipeBrowser:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def fetch(self, url: str, recipe=None) -> FetchResult:
        self.calls.append((url, recipe))
        assert recipe is not None
        return FetchResult(
            url=url,
            final_url="https://dom.mingkh.ru/perm/perm/123456",
            status_code=200,
            content_type="text/html; charset=utf-8",
            text=(
                "<html><body>"
                "<h1>Пермь, Комсомольский проспект, 27</h1>"
                "<div>Количество жителей: 81</div>"
                "</body></html>"
            ),
            title="Дом",
            runtime="browser_recipe",
            metadata={
                "recipe_id": recipe.recipe_id,
                "recipe_version": recipe.version,
            },
        )


class _NonGoalResidentialRecipeBrowser(_ResidentialRecipeBrowser):
    async def fetch(self, url: str, recipe=None) -> FetchResult:
        self.calls.append((url, recipe))
        assert recipe is not None
        return FetchResult(
            url=url,
            final_url="https://dom.mingkh.ru/perm/perm/123456",
            status_code=200,
            content_type="text/html; charset=utf-8",
            text=(
                "<html><body>"
                "<h1>Пермь, Комсомольский проспект, 27</h1>"
                "<div>Количество квартир: 32</div>"
                "</body></html>"
            ),
            title="Дом",
            runtime="browser_recipe",
            metadata={
                "recipe_id": recipe.recipe_id,
                "recipe_version": recipe.version,
            },
        )


class _RecipeAgent:
    name = "test-recipe-agent"

    async def run(self, task) -> AgentResult:
        assert task.context["page_url"] == "https://dom.mingkh.ru/search"
        candidates = task.context["research_input_candidates"]
        assert "Пермь, Комсомольский проспект, 27" in candidates
        assert not any("site:dom.mingkh.ru" in str(value) for value in candidates)
        assert not any("Количество жителей" in str(value) for value in candidates)
        return AgentResult(
            success=True,
            data={},
            visited_urls=[task.url],
            actions=[{"scroll": {"pixels": 500}}],
            metadata={
                "status": "success",
                "code": "AGENT_OK",
                "backend": self.name,
                "agent_output_is_evidence": False,
            },
        )


class _Snapshots:
    async def capture(self, *args, **kwargs):
        from types import SimpleNamespace

        return SimpleNamespace(snapshot_id="snapshot-residential-recipe")


def _request(*intents: str) -> CollectionRequest:
    return CollectionRequest(
        consumer="consumer-neutral-test",
        analysis_id="analysis-residential-recipe",
        territory={"city": "Пермь", "address": "Комсомольский проспект, 27"},
        intents=list(intents or ("residential_population",)),
    )


def _task() -> SourceTask:
    return SourceTask(
        source_id="mingkh_residential",
        goal="residential_population",
        url="https://dom.mingkh.ru/search",
        metadata={
            "collection_id": "collection-residential-recipe",
            "research_goals": ["residential_population"],
            "allowed_domains": ["dom.mingkh.ru"],
            "research_input_candidates": [
                'site:dom.mingkh.ru "Пермь, Комсомольский проспект, 27" "Количество жителей"'
            ],
        },
    )


def _initial_page() -> FetchResult:
    return FetchResult(
        url="https://dom.mingkh.ru/search",
        final_url="https://dom.mingkh.ru/search",
        status_code=200,
        content_type="text/html; charset=utf-8",
        text=(
            "<html><body><h1>Поиск дома</h1>"
            "<form method='get'><label>Адрес<input name='address'></label></form>"
            "</body></html>"
        ),
        title="Поиск дома",
        runtime="browser",
    )


def _web(repository, browser, recipes):
    return LifecycleRecipeWebAdapter(
        repository=repository,
        fast=_FastUnused(),
        browser=browser,
        snapshots=SnapshotService(repository),
        recipes=recipes,
        agent=_RecipeAgent(),
        pdf_extractor=BoundedPdfExtractor(
            max_bytes=1_000_000,
            max_pages=10,
            max_text_chars=100_000,
            timeout_seconds=1.0,
            memory_mb=128,
        ),
    )


@pytest.mark.asyncio
async def test_residential_recipe_becomes_active_only_after_source_fact(tmp_path: Path):
    repository = LifecycleAtomicSQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    browser = _ResidentialRecipeBrowser()
    recipes = RecipeManager(
        repository,
        failure_threshold=3,
        max_age_days=30,
        keep_versions=10,
    )
    web = _web(repository, browser, recipes)
    adapter = MingkhResidentialAdapter(web, _Snapshots())
    task = _task()

    result = await adapter.extract(task, _initial_page(), _request())

    assert len(browser.calls) == 1
    assert len(result.observations) == 1
    fact = result.observations[0]
    assert fact.data["intent"] == "residential_population"
    assert fact.data["value"] == 81
    assert fact.provenance["recipe_id"]

    stored = await repository.get_recipe("dom.mingkh.ru", "residential_population")
    assert stored is not None
    assert stored.recipe_id == fact.provenance["recipe_id"]
    assert stored.status == "active"
    assert stored.verified_at is not None
    assert stored.successes == 1

    verification = task.metadata["recipe_goal_verification"]
    assert verification["source_backed"] is True
    candidates = verification["candidates"]
    assert candidates[-1]["goal_verified"] is True
    assert candidates[-1]["status"] == "active"
    await repository.close()


@pytest.mark.asyncio
async def test_non_goal_residential_fact_does_not_activate_population_recipe(tmp_path: Path):
    repository = LifecycleAtomicSQLiteRepository(tmp_path / "argus-non-goal.sqlite")
    await repository.initialize()
    browser = _NonGoalResidentialRecipeBrowser()
    recipes = RecipeManager(
        repository,
        failure_threshold=3,
        max_age_days=30,
        keep_versions=10,
    )
    web = _web(repository, browser, recipes)
    adapter = MingkhResidentialAdapter(web, _Snapshots())
    task = _task()

    result = await adapter.extract(
        task,
        _initial_page(),
        _request("residential_population", "residential_premises_count"),
    )

    assert len(browser.calls) == 1
    assert {
        item.data["intent"]: item.data["value"] for item in result.observations
    } == {"residential_premises_count": 32}
    assert task.metadata["mingkh_interface_navigation_result"] == (
        "non_goal_source_fact_revealed"
    )

    stored = await repository.get_recipe("dom.mingkh.ru", "residential_population")
    assert stored is None
    verification = task.metadata["recipe_goal_verification"]
    assert verification["source_backed"] is False
    candidates = verification["candidates"]
    assert candidates[-1]["goal_verified"] is False
    assert candidates[-1]["status"] == "invalidated"
    assert candidates[-1]["reason"] == "semantic_goal_not_satisfied"
    await repository.close()
