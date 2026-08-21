from pathlib import Path

import pytest

from argus.crawler.agent.base import AgentResult
from argus.crawler.models import FetchResult
from argus.recipes.models import RecipeStep, SiteRecipe
from argus.recipes.service import RecipeManager
from argus.sources.base import SourceTask
from argus.sources.generic_web import GenericWebAdapter
from argus.storage.sqlite import SQLiteRepository


class FailingFast:
    def __init__(self) -> None:
        self.calls = 0

    async def fetch(self, url: str):
        del url
        self.calls += 1
        raise RuntimeError("FAST cannot operate this interface")


class FakeBrowser:
    def __init__(self, *, fail_plain: bool = True, fail_recipe_versions: set[int] | None = None):
        self.fail_plain = fail_plain
        self.fail_recipe_versions = fail_recipe_versions or set()
        self.calls: list[tuple[str, int | None]] = []

    async def fetch(self, url: str, recipe=None):
        version = recipe.version if recipe is not None else None
        self.calls.append((url, version))
        if recipe is None and self.fail_plain:
            raise RuntimeError("generic browser path cannot find the target view")
        if recipe is not None and recipe.version in self.fail_recipe_versions:
            raise RuntimeError("recipe selector no longer matches")
        metadata = {}
        runtime = "browser"
        if recipe is not None:
            runtime = "browser_recipe"
            metadata = {"recipe_id": recipe.recipe_id, "recipe_version": recipe.version}
        return FetchResult(
            url=url,
            final_url="https://example.com/results",
            status_code=200,
            content_type="text/html",
            text="<html><body>source-backed result</body></html>",
            runtime=runtime,
            metadata=metadata,
        )


class FakeAgent:
    name = "fake-agent"

    def __init__(self) -> None:
        self.calls = 0

    async def run(self, task):
        self.calls += 1
        return AgentResult(
            success=True,
            data={"result": "LLM summary must not become an observation"},
            visited_urls=[task.url, "https://example.com/results"],
            actions=[
                {
                    "input_text": {"text": "Пушкинская 277"},
                    "interacted_element": {
                        "node_name": "input",
                        "attributes": {"id": "search"},
                    },
                },
                {
                    "click_element_by_index": {"index": 3},
                    "interacted_element": {
                        "node_name": "button",
                        "attributes": {"data-testid": "submit"},
                    },
                },
                {"done": {"success": True}, "interacted_element": None},
            ],
        )


async def build_adapter(tmp_path: Path, browser: FakeBrowser, agent: FakeAgent):
    repository = SQLiteRepository(tmp_path / "db.sqlite")
    await repository.initialize()
    recipes = RecipeManager(repository)
    fast = FailingFast()
    adapter = GenericWebAdapter(
        fast=fast,
        browser=browser,
        snapshots=None,
        recipes=recipes,
        agent=agent,
    )
    return recipes, fast, adapter


@pytest.mark.asyncio
async def test_agent_path_is_compiled_verified_and_reused(tmp_path: Path):
    browser = FakeBrowser()
    agent = FakeAgent()
    recipes, fast, adapter = await build_adapter(tmp_path, browser, agent)
    task = SourceTask(
        source_id="generic_web",
        goal="reviews",
        url="https://example.com/",
        metadata={"allowed_domains": ["example.com"]},
    )

    first = await adapter.fetch(task)
    saved = await recipes.get(task.url, task.goal)
    assert saved is not None and saved.version == 1
    assert saved.last_success_at is not None
    assert "source-backed result" in first.text
    assert first.metadata["agent_compiled_recipe"] is True
    assert agent.calls == 1
    assert fast.calls == 1

    second = await adapter.fetch(task)
    assert second.runtime == "browser_recipe"
    assert agent.calls == 1
    assert fast.calls == 1


@pytest.mark.asyncio
async def test_broken_recipe_is_replaced_by_verified_v2(tmp_path: Path):
    browser = FakeBrowser(fail_recipe_versions={1})
    agent = FakeAgent()
    recipes, fast, adapter = await build_adapter(tmp_path, browser, agent)
    await recipes.save(
        SiteRecipe(
            domain="example.com",
            goal="reviews",
            version=1,
            steps=[RecipeStep(action="click", selector="#old-reviews")],
        )
    )
    task = SourceTask(
        source_id="generic_web",
        goal="reviews",
        url="https://example.com/",
        metadata={"allowed_domains": ["example.com"]},
    )

    result = await adapter.fetch(task)
    latest = await recipes.get(task.url, task.goal)
    assert result.runtime == "browser_recipe"
    assert latest is not None and latest.version == 2
    assert latest.last_success_at is not None
    assert agent.calls == 1
    assert fast.calls == 0
