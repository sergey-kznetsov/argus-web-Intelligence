from __future__ import annotations

import pytest

from argus.config import Settings
from argus.crawler.browser.runtime import BrowserCrawlerRuntime
from argus.security.urls import UrlGuard


class NavigationRaceError(RuntimeError):
    pass


class FakeLocator:
    def __init__(self, *, values=None, failures: int = 0, text: str = "") -> None:
        self.values = values or []
        self.failures = failures
        self.text = text

    async def evaluate_all(self, script: str):
        del script
        if self.failures > 0:
            self.failures -= 1
            raise NavigationRaceError(
                "Execution context was destroyed, most likely because of a navigation"
            )
        return list(self.values)

    async def inner_text(self) -> str:
        if self.failures > 0:
            self.failures -= 1
            raise NavigationRaceError(
                "Execution context was destroyed, most likely because of a navigation"
            )
        return self.text


class FakePage:
    def __init__(self, links: FakeLocator, body: FakeLocator) -> None:
        self.links = links
        self.body = body
        self.wait_calls = 0

    def locator(self, selector: str):
        return self.links if selector == "a[href]" else self.body

    async def wait_for_load_state(self, state: str, timeout: int) -> None:
        assert state == "domcontentloaded"
        assert timeout == 1500
        self.wait_calls += 1


def runtime() -> BrowserCrawlerRuntime:
    return BrowserCrawlerRuntime(Settings(), UrlGuard.from_strings([]))


@pytest.mark.asyncio
async def test_link_extraction_recovers_after_navigation_context_replacement() -> None:
    page = FakePage(
        FakeLocator(values=["https://example.test/a"], failures=1),
        FakeLocator(text="body"),
    )

    links = await runtime()._page_links(page)

    assert links == ["https://example.test/a"]
    assert page.wait_calls == 1


@pytest.mark.asyncio
async def test_link_extraction_degrades_to_empty_after_repeated_navigation_races() -> None:
    page = FakePage(
        FakeLocator(values=["https://example.test/a"], failures=3),
        FakeLocator(text="body"),
    )

    assert await runtime()._page_links(page) == []
    assert page.wait_calls == 2


@pytest.mark.asyncio
async def test_body_extraction_falls_back_to_html_after_repeated_navigation_races() -> None:
    page = FakePage(
        FakeLocator(values=[]),
        FakeLocator(text="body", failures=3),
    )

    text = await runtime()._page_body_text(page, "<html>fallback</html>")

    assert text == "<html>fallback</html>"
    assert page.wait_calls == 2


def test_navigation_context_detection_is_narrow() -> None:
    rt = runtime()

    assert rt._is_navigation_context_error(
        NavigationRaceError("Execution context was destroyed, most likely because of a navigation")
    )
    assert not rt._is_navigation_context_error(RuntimeError("certificate validation failed"))
