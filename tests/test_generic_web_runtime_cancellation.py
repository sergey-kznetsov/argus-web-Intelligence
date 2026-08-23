from __future__ import annotations

import asyncio

import pytest

from argus.crawler.models import FetchResult
from argus.sources.base import SourceTask
from argus.sources.generic_web import GenericWebAdapter


class UnusedSnapshots:
    pass


class BlockingFast:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def fetch(self, url: str):
        del url
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


class BrowserProbe:
    def __init__(self, *, block: bool) -> None:
        self.block = block
        self.calls = 0
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def fetch(self, url: str, recipe=None):
        del recipe
        self.calls += 1
        self.started.set()
        if self.block:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise
        return FetchResult(
            url=url,
            final_url=url,
            status_code=200,
            content_type="text/html",
            text="browser-result",
            blocked=False,
            runtime="browser:test",
        )


class BrowserRequiredFast:
    async def fetch(self, url: str):
        return FetchResult(
            url=url,
            final_url=url,
            status_code=200,
            content_type="text/html",
            text="<html><body>Please enable JavaScript to continue</body></html>",
            blocked=False,
            runtime="fast:test",
        )


class AgentProbe:
    name = "agent-probe"

    def __init__(self) -> None:
        self.calls = 0

    async def run(self, task):
        del task
        self.calls += 1
        raise AssertionError("agent must not run after task cancellation")


def source_task() -> SourceTask:
    return SourceTask(
        source_id="generic_web",
        goal="public_mentions",
        url="https://example.com/cancellation",
    )


@pytest.mark.asyncio
async def test_fast_cancellation_propagates_without_browser_fallback():
    fast = BlockingFast()
    browser = BrowserProbe(block=False)
    adapter = GenericWebAdapter(
        fast=fast,  # type: ignore[arg-type]
        browser=browser,  # type: ignore[arg-type]
        snapshots=UnusedSnapshots(),  # type: ignore[arg-type]
    )

    execution = asyncio.create_task(adapter.fetch(source_task()))
    await asyncio.wait_for(fast.started.wait(), timeout=2)
    execution.cancel()

    with pytest.raises(asyncio.CancelledError):
        await execution
    await asyncio.wait_for(fast.cancelled.wait(), timeout=2)
    assert browser.calls == 0


@pytest.mark.asyncio
async def test_browser_cancellation_propagates_without_agent_fallback():
    browser = BrowserProbe(block=True)
    agent = AgentProbe()
    adapter = GenericWebAdapter(
        fast=BrowserRequiredFast(),  # type: ignore[arg-type]
        browser=browser,  # type: ignore[arg-type]
        snapshots=UnusedSnapshots(),  # type: ignore[arg-type]
        agent=agent,  # type: ignore[arg-type]
    )

    execution = asyncio.create_task(adapter.fetch(source_task()))
    await asyncio.wait_for(browser.started.wait(), timeout=2)
    execution.cancel()

    with pytest.raises(asyncio.CancelledError):
        await execution
    await asyncio.wait_for(browser.cancelled.wait(), timeout=2)
    assert browser.calls == 1
    assert agent.calls == 0
