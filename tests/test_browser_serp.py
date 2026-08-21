from types import SimpleNamespace

import pytest

from argus.config import Settings
from argus.contracts.models import CollectionRequest
from argus.research.browser_serp import DuckDuckGoBrowserDiscoveryProvider
from argus.research.discovery import DiscoveryBlockedError


class FakeBrowser:
    def __init__(self, html: str, *, blocked: bool = False) -> None:
        self.html = html
        self.blocked = blocked
        self.calls = []

    async def fetch(self, url, recipe=None):
        self.calls.append((url, recipe))
        return SimpleNamespace(text=self.html, blocked=self.blocked)


def request():
    return CollectionRequest(
        consumer="test",
        analysis_id="1",
        territory={"city": "Ижевск"},
        intents=["public_mentions"],
    )


@pytest.mark.asyncio
async def test_browser_serp_extracts_destination_urls_only():
    html = """
    <html><body>
      <a href="/l/?uddg=https%3A%2F%2Fexample.com%2Fa">Example A</a>
      <a href="https://docs.python.org/3/">Python</a>
      <a href="https://html.duckduckgo.com/about">Internal</a>
      <a href="/l/?uddg=https%3A%2F%2Fexample.com%2Fa">Duplicate</a>
    </body></html>
    """
    browser = FakeBrowser(html)
    provider = DuckDuckGoBrowserDiscoveryProvider(
        Settings(browser_serp_max_results_per_query=5),
        browser,
    )
    hits = await provider.discover(["Ижевск отзывы"], request())
    assert [hit.url for hit in hits] == [
        "https://example.com/a",
        "https://docs.python.org/3/",
    ]
    _, recipe = browser.calls[0]
    assert recipe.steps[0].selector == 'input[name="q"]'
    assert recipe.steps[0].value == "Ижевск отзывы"


@pytest.mark.asyncio
async def test_browser_serp_reports_antibot_as_blocked():
    browser = FakeBrowser(
        "<html><body>Unfortunately, bots use DuckDuckGo too.</body></html>"
    )
    provider = DuckDuckGoBrowserDiscoveryProvider(Settings(), browser)
    with pytest.raises(DiscoveryBlockedError):
        await provider.discover(["query"], request())
