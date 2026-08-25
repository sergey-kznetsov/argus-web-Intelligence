from __future__ import annotations

from pathlib import Path

import pytest

from argus.crawler.agent.base import AgentResult
from argus.crawler.models import FetchResult
from argus.extraction.pdf import BoundedPdfExtractor
from argus.history.snapshots import SnapshotService
from argus.recipes.service import RecipeManager
from argus.sources.base import SourceTask
from argus.sources.recipe_web import LifecycleRecipeWebAdapter
from argus.storage.lifecycle_sqlite import LifecycleAtomicSQLiteRepository


class FastUnused:
    async def fetch(self, url: str) -> FetchResult:
        raise AssertionError(f"FAST should not be used directly in this test: {url}")


class BrowserSpy:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[tuple[str, object]] = []
        self.fail = fail

    async def fetch(self, url: str, recipe=None) -> FetchResult:
        self.calls.append((url, recipe))
        if self.fail:
            raise RuntimeError("browser fetch failed")
        return FetchResult(
            url=url,
            final_url=url,
            status_code=200,
            content_type="text/html",
            text="<html><body>public result</body></html>",
            runtime="browser_recipe" if recipe is not None else "browser",
        )


class UnsupportedActionAgent:
    name = "test-agent"

    async def run(self, task) -> AgentResult:
        del task
        return AgentResult(
            success=True,
            data={},
            visited_urls=[
                "https://example.com/source",
                "https://example.com/alternate",
            ],
            actions=[{"upload_file": {"path": "/tmp/private"}}],
            metadata={"reason_code": "AGENT_OK"},
        )


class ActionFreeAgent:
    name = "test-agent"

    def __init__(self, visited_urls: list[str]) -> None:
        self.visited_urls = visited_urls

    async def run(self, task) -> AgentResult:
        del task
        return AgentResult(
            success=True,
            data={},
            visited_urls=list(self.visited_urls),
            actions=[],
            metadata={"reason_code": "AGENT_OK"},
        )


def source_task() -> SourceTask:
    return SourceTask(
        source_id="generic_web",
        goal="public_mentions",
        url="https://example.com/source",
        metadata={
            "collection_id": "agent-path-verification",
            "allowed_domains": ["example.com"],
        },
    )


def build_adapter(repository, *, browser, agent) -> LifecycleRecipeWebAdapter:
    return LifecycleRecipeWebAdapter(
        repository=repository,
        fast=FastUnused(),
        browser=browser,
        snapshots=SnapshotService(repository),
        recipes=RecipeManager(repository),
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
async def test_successful_agent_with_uncompilable_actions_does_not_fallback_to_visited_url(
    tmp_path: Path,
):
    repository = LifecycleAtomicSQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    browser = BrowserSpy()
    web = build_adapter(repository, browser=browser, agent=UnsupportedActionAgent())
    task = source_task()

    result = await web._agent_guided_fetch(task)

    assert result is None
    assert browser.calls == []
    assert task.metadata["agent_path_rejected"] == "actions_not_deterministically_compilable"
    assert task.metadata["agent_execution"]["reason_code"] == "AGENT_OK"
    await repository.close()


@pytest.mark.asyncio
async def test_action_free_agent_direct_replay_is_bounded(tmp_path: Path):
    repository = LifecycleAtomicSQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    browser = BrowserSpy(fail=True)
    agent = ActionFreeAgent(
        [
            "https://example.com/source",
            "https://example.com/a",
            "https://example.com/b",
            "https://example.com/c",
            "https://example.com/d",
        ]
    )
    web = build_adapter(repository, browser=browser, agent=agent)

    result = await web._agent_guided_fetch(source_task())

    assert result is None
    assert len(browser.calls) == web.max_agent_direct_replay_urls
    assert [call[0] for call in browser.calls] == [
        "https://example.com/d",
        "https://example.com/c",
    ]
    await repository.close()


@pytest.mark.asyncio
async def test_action_free_agent_result_is_refetched_before_becoming_factual_input(tmp_path: Path):
    repository = LifecycleAtomicSQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    browser = BrowserSpy()
    agent = ActionFreeAgent(
        [
            "https://example.com/source",
            "https://example.com/public-result",
        ]
    )
    web = build_adapter(repository, browser=browser, agent=agent)

    result = await web._agent_guided_fetch(source_task())

    assert result is not None
    assert result.runtime == "browser"
    assert result.final_url == "https://example.com/public-result"
    assert result.metadata["agent_guided"] is True
    assert result.metadata["agent_direct_replay_bounded"] is True
    assert result.metadata["agent_execution"]["reason_code"] == "AGENT_OK"
    await repository.close()
