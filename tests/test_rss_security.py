from types import SimpleNamespace

import pytest

from argus.contracts.models import CollectionRequest
from argus.crawler.models import FetchResult
from argus.sources.base import SourceTask
from argus.sources.rss import RSSAdapter


class FakeSnapshots:
    async def capture(self, source_id, url, text, content_type):
        del source_id, url, text, content_type
        return SimpleNamespace(snapshot_id="snapshot-1")


class FakeFast:
    async def fetch(self, url):
        raise AssertionError(f"unexpected fetch: {url}")


def request() -> CollectionRequest:
    return CollectionRequest(
        consumer="test",
        analysis_id="1",
        territory={"city": "Ижевск"},
        intents=["local_news"],
    )


def task() -> SourceTask:
    return SourceTask(
        source_id="rss_atom",
        goal="local_news",
        url="https://example.com/feed.xml",
        metadata={"collection_id": "collection-1"},
    )


def fetched(text: str) -> FetchResult:
    return FetchResult(
        url="https://example.com/feed.xml",
        final_url="https://example.com/feed.xml",
        status_code=200,
        content_type="application/xml",
        text=text,
    )


@pytest.mark.asyncio
async def test_rss_still_extracts_normal_feed_entries_with_feed_evidence_provenance():
    xml = """<?xml version="1.0"?>
    <rss version="2.0"><channel><item>
      <title>Новость</title>
      <description>Описание</description>
      <link>https://example.com/news/1</link>
      <guid>news-1</guid>
    </item></channel></rss>"""
    adapter = RSSAdapter(FakeFast(), FakeSnapshots())
    result = await adapter.extract(task(), fetched(xml), request())

    assert result.errors == []
    assert len(result.observations) == 1
    observation = result.observations[0]
    assert observation.title == "Новость"
    assert observation.url == "https://example.com/news/1"
    assert observation.provenance["feed_url"] == "https://example.com/feed.xml"
    assert len(result.evidence) == 1
    evidence = result.evidence[0]
    assert evidence.source.url == "https://example.com/feed.xml"
    assert evidence.metadata["entry_url"] == "https://example.com/news/1"


@pytest.mark.asyncio
async def test_rss_rejects_dtd_entity_payload_without_expansion():
    xml = """<!DOCTYPE rss [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
    <rss version="2.0"><channel><item>
      <title>&xxe;</title>
      <description>unsafe</description>
    </item></channel></rss>"""
    adapter = RSSAdapter(FakeFast(), FakeSnapshots())
    result = await adapter.extract(task(), fetched(xml), request())

    assert result.observations == []
    assert result.evidence == []
    assert [error.code for error in result.errors] == ["FEED_XML_INVALID"]


@pytest.mark.asyncio
async def test_rss_non_http_entry_link_falls_back_to_fetched_feed_url():
    xml = """<?xml version="1.0"?>
    <rss version="2.0"><channel><item>
      <title>Новость</title>
      <description>Описание</description>
      <link>file:///etc/passwd</link>
    </item></channel></rss>"""
    adapter = RSSAdapter(FakeFast(), FakeSnapshots())
    result = await adapter.extract(task(), fetched(xml), request())

    assert result.observations[0].url == "https://example.com/feed.xml"
    assert result.evidence[0].source.url == "https://example.com/feed.xml"
