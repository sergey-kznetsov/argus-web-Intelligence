from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.contracts.models import CollectionRequest
from argus.extraction.structured_data import BoundedStructuredDataExtractor
from argus.history.snapshots import SnapshotService
from argus.sources.base import SourceTask
from argus.sources.generic_web import GenericWebAdapter
from argus.sources.json_feed import JSONFeedAdapter
from argus.storage.sqlite import SQLiteRepository


class FastStub:
    pass


class BrowserStub:
    pass


def request(*, seed_urls=None) -> CollectionRequest:
    constraints = {}
    if seed_urls is not None:
        constraints["seed_urls"] = seed_urls
    return CollectionRequest(
        consumer="json-feed-test",
        analysis_id="analysis-json-feed",
        territory={"city": "Ижевск"},
        intents=["local_news"],
        constraints=constraints,
    )


def fetched(payload: object, *, url: str = "https://example.com/feed.json"):
    text = json.dumps(payload, ensure_ascii=False)
    return SimpleNamespace(
        blocked=False,
        final_url=url,
        text=text,
        body=text.encode("utf-8"),
        content_type="application/feed+json; charset=utf-8",
    )


async def adapter(tmp_path: Path, *, max_items: int = 100, max_json_nodes: int = 20_000):
    repository = SQLiteRepository(tmp_path / "json-feed.sqlite")
    await repository.initialize()
    return JSONFeedAdapter(
        FastStub(),  # type: ignore[arg-type]
        SnapshotService(repository),
        BoundedStructuredDataExtractor(max_json_nodes=max_json_nodes),
        max_items=max_items,
    ), repository


@pytest.mark.asyncio
async def test_json_feed_extracts_text_html_numeric_id_and_provenance(tmp_path: Path):
    source, repository = await adapter(tmp_path)
    task = SourceTask(
        source_id="json_feed",
        goal="local_news",
        url="https://example.com/feed.json",
        metadata={
            "collection_id": "json-feed-c1",
            "research_goals": ["local_news", "public_mentions"],
        },
    )
    payload = {
        "version": "https://jsonfeed.org/version/1.1",
        "title": "Новости района",
        "items": [
            {
                "id": "post-1",
                "url": "/news/1",
                "title": "Первая публикация",
                "content_text": "Фактический текст",
                "date_published": "2026-08-23T10:30:00+04:00",
                "tags": ["город"],
            },
            {
                "id": 2,
                "url": "https://example.com/news/2",
                "content_html": "<p>Вторая <b>публикация</b></p><script>bad()</script>",
            },
        ],
    }

    result = await source.extract(task, fetched(payload), request())

    assert result.partial is False
    assert result.errors == []
    assert len(result.observations) == 2
    assert len(result.evidence) == 2
    first, second = result.observations
    assert first.entity_id == "post-1"
    assert first.url == "https://example.com/news/1"
    assert first.title == "Первая публикация"
    assert first.text == "Фактический текст"
    assert first.published_at is not None
    assert first.source_kind == "json_feed_item"
    assert first.provenance["feed_url"] == "https://example.com/feed.json"
    assert first.provenance["research_goals"] == ["local_news", "public_mentions"]
    assert first.quality["machine_readable"] is True
    assert second.entity_id == "2"
    assert second.text == "Вторая\nпубликация"
    assert "bad()" not in second.text
    assert result.evidence[0].type == "json_feed_item"
    assert '"id":"post-1"' in result.evidence[0].text

    snapshot = await repository.latest_snapshot("https://example.com/feed.json")
    assert snapshot is not None


@pytest.mark.asyncio
async def test_json_feed_skips_invalid_items_and_marks_partial(tmp_path: Path):
    source, _ = await adapter(tmp_path)
    task = SourceTask(
        source_id="json_feed",
        goal="local_news",
        url="https://example.com/feed.json",
        metadata={"collection_id": "json-feed-c2"},
    )
    payload = {
        "version": "https://jsonfeed.org/version/1",
        "title": "Feed",
        "items": [
            {"id": "good", "content_text": "Valid"},
            {"id": "missing-content"},
            {"content_text": "missing id"},
        ],
    }

    result = await source.extract(task, fetched(payload), request())

    assert len(result.observations) == 1
    assert result.partial is True
    assert [error.code for error in result.errors] == ["JSON_FEED_ITEM_INVALID"]
    assert "2 invalid item" in result.errors[0].message


@pytest.mark.asyncio
async def test_json_feed_rejects_wrong_version_before_snapshot(tmp_path: Path):
    source, repository = await adapter(tmp_path)
    task = SourceTask(
        source_id="json_feed",
        goal="local_news",
        url="https://example.com/feed.json",
        metadata={"collection_id": "json-feed-c3"},
    )
    payload = {
        "version": "https://jsonfeed.org/version/9",
        "title": "Feed",
        "items": [],
    }

    result = await source.extract(task, fetched(payload), request())

    assert result.observations == []
    assert [error.code for error in result.errors] == ["JSON_FEED_INVALID"]
    assert await repository.latest_snapshot("https://example.com/feed.json") is None


@pytest.mark.asyncio
async def test_json_feed_item_limit_is_explicit_partial_coverage(tmp_path: Path):
    source, _ = await adapter(tmp_path, max_items=1)
    task = SourceTask(
        source_id="json_feed",
        goal="local_news",
        url="https://example.com/feed.json",
        metadata={"collection_id": "json-feed-c4"},
    )
    payload = {
        "version": "https://jsonfeed.org/version/1.1",
        "title": "Feed",
        "items": [
            {"id": "1", "content_text": "One"},
            {"id": "2", "content_text": "Two"},
        ],
    }

    result = await source.extract(task, fetched(payload), request())

    assert len(result.observations) == 1
    assert result.partial is True
    assert [error.code for error in result.errors] == ["JSON_FEED_ITEM_LIMIT"]


@pytest.mark.asyncio
async def test_json_feed_uses_shared_structural_limits(tmp_path: Path):
    source, _ = await adapter(tmp_path, max_json_nodes=4)
    task = SourceTask(
        source_id="json_feed",
        goal="local_news",
        url="https://example.com/feed.json",
        metadata={"collection_id": "json-feed-c5"},
    )
    payload = {
        "version": "https://jsonfeed.org/version/1.1",
        "title": "Feed",
        "items": [{"id": "1", "content_text": "One"}],
    }

    result = await source.extract(task, fetched(payload), request())

    assert result.observations == []
    assert [error.code for error in result.errors] == ["STRUCTURED_DATA_LIMIT_EXCEEDED"]


@pytest.mark.asyncio
async def test_json_feed_direct_discovery_is_narrow_and_avoids_generic_json():
    source = JSONFeedAdapter(
        FastStub(),  # type: ignore[arg-type]
        SimpleNamespace(),  # type: ignore[arg-type]
        BoundedStructuredDataExtractor(),
    )
    result = await source.discover(
        request(
            seed_urls=[
                "https://example.com/feed.json",
                "https://example.com/news.feed.json",
                "https://example.com/stream.jsonfeed",
                "https://example.com/api/data.json",
            ]
        )
    )

    assert [task.url for task in result] == [
        "https://example.com/feed.json",
        "https://example.com/news.feed.json",
        "https://example.com/stream.jsonfeed",
    ]


def test_generic_web_discovers_standard_json_feed_alternate():
    generic = GenericWebAdapter(
        FastStub(),  # type: ignore[arg-type]
        BrowserStub(),  # type: ignore[arg-type]
        SimpleNamespace(),  # type: ignore[arg-type]
    )
    html = """
    <html><head>
      <link rel="alternate" type="application/rss+xml" href="/rss.xml">
      <link rel="alternate" type="application/feed+json; charset=utf-8" href="/feed.json#top">
      <link rel="alternate" type="application/json" href="/not-a-feed.json">
    </head></html>
    """
    task = SourceTask(
        source_id="generic_web",
        goal="local_news",
        url="https://example.com/",
    )
    page = SimpleNamespace(
        text=html,
        final_url="https://example.com/",
        content_type="text/html",
        links=[],
    )
    result = generic._discovered_tasks(task, page, request(), "json-feed-c6")

    by_source = {(item.source_id, item.url) for item in result}
    assert ("rss_atom", "https://example.com/rss.xml") in by_source
    assert ("json_feed", "https://example.com/feed.json") in by_source
    assert ("json_feed", "https://example.com/not-a-feed.json") not in by_source
