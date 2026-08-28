from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from argus.config import Settings
from argus.contracts.models import CollectionRequest
from argus.research.browser_serp import DuckDuckGoFastDiscoveryProvider
from argus.research.discovery import DiscoveryBlockedError


class FakeFast:
    def __init__(self, html: str, *, blocked: bool = False) -> None:
        self.html = html
        self.blocked = blocked
        self.calls: list[str] = []

    async def fetch(self, url: str):
        self.calls.append(url)
        return SimpleNamespace(text=self.html, blocked=self.blocked)


def request():
    return CollectionRequest(
        consumer="test",
        analysis_id="1",
        territory={"city": "Ижевск"},
        intents=["public_mentions"],
    )


@pytest.mark.asyncio
async def test_fast_serp_extracts_destination_urls_only():
    html = """
    <html><body>
      <a href="/l/?uddg=https%3A%2F%2Fexample.com%2Fa">Example A</a>
      <a href="https://docs.python.org/3/">Python</a>
      <a href="https://html.duckduckgo.com/about">Internal</a>
      <a href="/l/?uddg=https%3A%2F%2Fexample.com%2Fa">Duplicate</a>
    </body></html>
    """
    fast = FakeFast(html)
    provider = DuckDuckGoFastDiscoveryProvider(
        Settings(browser_serp_max_results_per_query=5),
        fast,
    )
    hits = await provider.discover(["Ижевск отзывы"], request())
    assert [hit.url for hit in hits] == [
        "https://example.com/a",
        "https://docs.python.org/3/",
    ]
    assert all(hit.query == "Ижевск отзывы" for hit in hits)
    assert all(hit.provider == "duckduckgo_fast" for hit in hits)
    query = parse_qs(urlsplit(fast.calls[0]).query)
    assert query["q"] == ["Ижевск отзывы"]


@pytest.mark.asyncio
async def test_fast_serp_reports_antibot_as_blocked():
    fast = FakeFast(
        "<html><body>Unfortunately, bots use DuckDuckGo too.</body></html>"
    )
    provider = DuckDuckGoFastDiscoveryProvider(Settings(), fast)
    with pytest.raises(DiscoveryBlockedError):
        await provider.discover(["query"], request())
