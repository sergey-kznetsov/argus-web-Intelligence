from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.contracts.models import CollectionRequest
from argus.history.snapshots import SnapshotService
from argus.sources.base import SourceTask
from argus.sources.rss import RSSAdapter
from argus.storage.sqlite import SQLiteRepository


class FastStub:
    pass


def request() -> CollectionRequest:
    return CollectionRequest(
        consumer="feed-test",
        analysis_id="analysis-feed-1",
        territory={"city": "Ижевск"},
        intents=["local_news"],
    )


def task(collection_id: str = "collection-feed-1") -> SourceTask:
    return SourceTask(
        source_id="rss_atom",
        goal="local_news",
        url="https://example.com/feed.xml",
        metadata={"collection_id": collection_id},
    )


def fetched(text: str, content_type: str = "application/rss+xml"):
    return SimpleNamespace(
        blocked=False,
        final_url="https://example.com/feed.xml",
        text=text,
        content_type=content_type,
    )


@pytest.mark.asyncio
async def test_rss_extracts_entries_with_snapshot_provenance(tmp_path: Path):
    repo = SQLiteRepository(tmp_path / "db.sqlite")
    await repo.initialize()
    adapter = RSSAdapter(FastStub(), SnapshotService(repo))
    xml = (
        "<rss><channel><item><title>A</title>"
        "<link>https://example.com/a</link><description>B</description>"
        "</item></channel></rss>"
    )

    result = await adapter.extract(task(), fetched(xml), request())

    assert result.partial is False
    assert result.errors == []
    assert len(result.observations) == 1
    observation = result.observations[0]
    assert observation.title == "A"
    assert observation.text == "B"
    assert observation.url == "https://example.com/a"
    assert observation.data["feed_format"] == "rss"
    assert observation.quality["lossless"] is True
    assert observation.provenance["xml_node_count"] > 0
    assert len(result.evidence) == 1
    assert result.evidence[0].metadata["snapshot_id"] == observation.provenance["snapshot_id"]
    await repo.close()


@pytest.mark.asyncio
async def test_atom_prefers_alternate_link_over_self_and_parses_published_date(tmp_path: Path):
    repo = SQLiteRepository(tmp_path / "db.sqlite")
    await repo.initialize()
    adapter = RSSAdapter(FastStub(), SnapshotService(repo))
    xml = """
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <title>Atom item</title>
        <link rel="self" href="https://example.com/feed.xml" />
        <link rel="alternate" href="https://example.com/article" />
        <id>tag:example.com,2026:1</id>
        <published>2026-08-20T10:00:00+04:00</published>
        <summary>Summary</summary>
      </entry>
    </feed>
    """

    result = await adapter.extract(
        task("collection-atom-1"),
        fetched(xml, "application/atom+xml"),
        request(),
    )

    assert len(result.observations) == 1
    observation = result.observations[0]
    assert observation.data["feed_format"] == "atom"
    assert observation.url == "https://example.com/article"
    assert observation.entity_id == "tag:example.com,2026:1"
    assert observation.published_at is not None
    assert observation.published_at.isoformat() == "2026-08-20T10:00:00+04:00"
    await repo.close()


@pytest.mark.asyncio
async def test_item_and_field_limits_are_explicitly_partial(tmp_path: Path):
    repo = SQLiteRepository(tmp_path / "db.sqlite")
    await repo.initialize()
    adapter = RSSAdapter(
        FastStub(),
        SnapshotService(repo),
        max_items=1,
        max_title_chars=3,
        max_entry_text_chars=4,
        max_identifier_chars=64,
    )
    xml = """
    <rss><channel>
      <item>
        <title>Long title</title>
        <description>Long description</description>
        <guid>entry-1</guid>
        <link>https://example.com/one</link>
      </item>
      <item>
        <title>Second</title>
        <description>Second description</description>
        <guid>entry-2</guid>
        <link>https://example.com/two</link>
      </item>
    </channel></rss>
    """

    result = await adapter.extract(
        task("collection-feed-limits"),
        fetched(xml),
        request(),
    )

    assert result.partial is True
    assert [error.code for error in result.errors] == ["FEED_EXTRACTION_TRUNCATED"]
    assert len(result.observations) == 1
    observation = result.observations[0]
    assert observation.title == "Lon"
    assert observation.text == "Long"
    assert observation.data["feed_entry_count"] == 2
    assert observation.data["entry_truncated"] is True
    assert observation.quality["lossless"] is False
    await repo.close()


@pytest.mark.asyncio
async def test_xml_node_limit_rejects_feed_before_semantic_extraction(tmp_path: Path):
    repo = SQLiteRepository(tmp_path / "db.sqlite")
    await repo.initialize()
    adapter = RSSAdapter(
        FastStub(),
        SnapshotService(repo),
        max_xml_nodes=4,
    )
    xml = "<rss><channel><item><title>A</title><description>B</description></item></channel></rss>"

    result = await adapter.extract(
        task("collection-feed-node-limit"),
        fetched(xml),
        request(),
    )

    assert result.partial is True
    assert result.observations == []
    assert [error.code for error in result.errors] == ["FEED_XML_LIMIT_EXCEEDED"]
    await repo.close()


@pytest.mark.asyncio
async def test_xml_depth_limit_rejects_deep_feed(tmp_path: Path):
    repo = SQLiteRepository(tmp_path / "db.sqlite")
    await repo.initialize()
    adapter = RSSAdapter(
        FastStub(),
        SnapshotService(repo),
        max_xml_depth=2,
    )
    xml = "<rss><channel><wrapper><item><title>A</title></item></wrapper></channel></rss>"

    result = await adapter.extract(
        task("collection-feed-depth-limit"),
        fetched(xml),
        request(),
    )

    assert result.partial is True
    assert result.observations == []
    assert [error.code for error in result.errors] == ["FEED_XML_LIMIT_EXCEEDED"]
    await repo.close()


@pytest.mark.asyncio
async def test_unsafe_entry_link_falls_back_to_feed_url(tmp_path: Path):
    repo = SQLiteRepository(tmp_path / "db.sqlite")
    await repo.initialize()
    adapter = RSSAdapter(FastStub(), SnapshotService(repo))
    xml = (
        "<rss><channel><item><title>A</title>"
        "<link>javascript:alert(1)</link><description>B</description>"
        "</item></channel></rss>"
    )

    result = await adapter.extract(
        task("collection-feed-unsafe-link"),
        fetched(xml),
        request(),
    )

    assert result.observations[0].url == "https://example.com/feed.xml"
    await repo.close()


@pytest.mark.asyncio
async def test_feed_xml_entities_are_rejected(tmp_path: Path):
    repo = SQLiteRepository(tmp_path / "db.sqlite")
    await repo.initialize()
    adapter = RSSAdapter(FastStub(), SnapshotService(repo))
    xml = """<?xml version="1.0"?>
    <!DOCTYPE rss [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
    <rss><channel><item><title>&xxe;</title></item></channel></rss>
    """

    result = await adapter.extract(
        task("collection-feed-xxe"),
        fetched(xml),
        request(),
    )

    assert result.observations == []
    assert [error.code for error in result.errors] == ["FEED_XML_INVALID"]
    await repo.close()
