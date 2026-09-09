from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from argus.config import Settings
from argus.contracts.models import CollectionRequest
from argus.research.browser_serp import (
    BingRssDiscoveryProvider,
    DuckDuckGoFastDiscoveryProvider,
    MojeekFastDiscoveryProvider,
)
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


@pytest.mark.asyncio
async def test_mojeek_serp_extracts_only_result_destinations_and_unquotes_query():
    html = """
    <html><body>
      <ul class="results-standard">
        <li>
          <h2><a href="https://forum.example/topic">Forum topic</a></h2>
          <a class="ob" href="https://forum.example/topic">forum.example</a>
          <p class="s">Residents discuss the street.</p>
        </li>
        <li>
          <h2><a href="https://city.example/news">City news</a></h2>
          <a class="ob" href="https://city.example/news">city.example</a>
        </li>
      </ul>
      <h2><a href="https://www.mojeek.com/support/">Internal</a></h2>
    </body></html>
    """
    fast = FakeFast(html)
    provider = MojeekFastDiscoveryProvider(
        Settings(browser_serp_max_results_per_query=5),
        fast,
    )

    hits = await provider.discover(
        ['site:forum.example "Ижевск" "Пушкинская улица"'],
        request(),
    )

    assert [hit.url for hit in hits] == [
        "https://forum.example/topic",
        "https://city.example/news",
    ]
    assert all(hit.provider == "mojeek_fast" for hit in hits)
    query = parse_qs(urlsplit(fast.calls[0]).query)
    assert query["q"] == ["site:forum.example Ижевск Пушкинская улица"]
    assert hits[0].query == "site:forum.example Ижевск Пушкинская улица"


@pytest.mark.asyncio
async def test_mojeek_serp_reports_search_wall_as_blocked():
    fast = FakeFast(
        "<html><body>Sorry your network appears to be sending automated queries.</body></html>"
    )
    provider = MojeekFastDiscoveryProvider(Settings(), fast)

    with pytest.raises(DiscoveryBlockedError):
        await provider.discover(["Ижевск форум"], request())


@pytest.mark.asyncio
async def test_bing_rss_extracts_external_destinations_and_unquotes_query():
    rss = b"""<?xml version="1.0" encoding="utf-8"?>
    <rss version="2.0"><channel>
      <item><title>Forum topic</title><link>https://forum.example/topic</link>
        <description>Navigation snippet must not become evidence.</description></item>
      <item><title>City news</title><link>https://city.example/news</link></item>
      <item><title>Bing internal</title><link>https://www.bing.com/help</link></item>
      <item><title>Duplicate</title><link>https://forum.example/topic</link></item>
    </channel></rss>"""
    seen_requests: list[httpx.Request] = []

    def handler(request_value: httpx.Request) -> httpx.Response:
        seen_requests.append(request_value)
        return httpx.Response(
            200,
            headers={"content-type": "application/rss+xml"},
            content=rss,
        )

    provider = BingRssDiscoveryProvider(
        Settings(browser_serp_max_results_per_query=5),
        transport=httpx.MockTransport(handler),
    )
    hits = await provider.discover(
        ['site:forum.example "Ижевск" "Пушкинская улица"'],
        request(),
    )

    assert [hit.url for hit in hits] == [
        "https://forum.example/topic",
        "https://city.example/news",
    ]
    assert [hit.title for hit in hits] == ["Forum topic", "City news"]
    assert all(hit.provider == "bing_rss" for hit in hits)
    assert all(hit.engines == ["bing"] for hit in hits)
    query = parse_qs(seen_requests[0].url.query.decode())
    assert query["q"] == ["site:forum.example Ижевск Пушкинская улица"]
    assert query["format"] == ["rss"]
    assert hits[0].query == "site:forum.example Ижевск Пушкинская улица"


@pytest.mark.asyncio
async def test_bing_rss_reports_access_denial_as_blocked():
    def handler(request_value: httpx.Request) -> httpx.Response:
        del request_value
        return httpx.Response(429, text="rate limited")

    provider = BingRssDiscoveryProvider(
        Settings(),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(DiscoveryBlockedError):
        await provider.discover(["Ижевск новости"], request())


@pytest.mark.asyncio
async def test_bing_rss_enforces_response_size_limit():
    oversized = b"x" * 2049

    def handler(request_value: httpx.Request) -> httpx.Response:
        del request_value
        return httpx.Response(200, content=oversized)

    provider = BingRssDiscoveryProvider(
        Settings(max_response_bytes=2048),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ValueError, match="exceeds configured limit"):
        await provider.discover(["Ижевск новости"], request())
