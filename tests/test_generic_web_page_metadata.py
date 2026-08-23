from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.contracts.models import CollectionRequest
from argus.history.snapshots import SnapshotService
from argus.sources.base import SourceTask
from argus.sources.generic_web import GenericWebAdapter
from argus.storage.sqlite import SQLiteRepository


class FastStub:
    pass


class BrowserStub:
    pass


@pytest.mark.asyncio
async def test_generic_web_emits_page_metadata_observation_and_evidence(tmp_path: Path):
    repository = SQLiteRepository(tmp_path / "page-metadata.sqlite")
    await repository.initialize()
    adapter = GenericWebAdapter(
        FastStub(),  # type: ignore[arg-type]
        BrowserStub(),  # type: ignore[arg-type]
        SnapshotService(repository),
    )
    request = CollectionRequest(
        consumer="page-metadata-test",
        analysis_id="analysis-page-metadata",
        territory={"city": "Ижевск"},
        intents=["local_news"],
    )
    task = SourceTask(
        source_id="generic_web",
        goal="local_news",
        url="https://example.com/source?id=1",
        metadata={
            "collection_id": "page-metadata-c1",
            "research_goals": ["local_news"],
        },
    )
    html = """
    <html><head>
      <title>Rendered title</title>
      <link rel="canonical" href="https://example.com/news/1">
      <meta property="og:title" content="Source-declared title">
      <meta property="og:description" content="Source-declared description">
      <meta property="article:published_time" content="2026-08-23T12:00:00+04:00">
      <meta name="DC.date" content="2026-08">
    </head><body><article>Main factual text</article></body></html>
    """
    fetched = SimpleNamespace(
        blocked=False,
        final_url="https://example.com/source?id=1",
        text=html,
        content_type="text/html; charset=utf-8",
        title="Rendered title",
        runtime="fast:test",
        status_code=200,
        metadata={},
        links=[],
    )

    result = await adapter.extract(task, fetched, request)

    page = next(item for item in result.observations if item.source_kind == "web_page")
    metadata = next(item for item in result.observations if item.source_kind == "page_metadata")
    metadata_evidence = next(item for item in result.evidence if item.type == "page_metadata")

    assert page.url == "https://example.com/source?id=1"
    assert page.text == "Rendered title\nMain factual text"
    assert page.data["page_metadata_summary"] == {"fields": 5, "truncated": False}

    assert metadata.url == "https://example.com/source?id=1"
    assert metadata.entity_id == "https://example.com/news/1"
    assert metadata.title == "Source-declared title"
    assert metadata.text == "Source-declared description"
    assert metadata.published_at is not None
    assert metadata.published_at.isoformat() == "2026-08-23T12:00:00+04:00"
    assert metadata.data["dc_date"] == "2026-08"
    assert metadata.quality["source_declared"] is True
    assert metadata.provenance["canonical_url"] == "https://example.com/news/1"
    assert metadata.provenance["snapshot_id"] == page.provenance["snapshot_id"]

    assert metadata_evidence.observation_id == metadata.observation_id
    assert metadata_evidence.source.url == "https://example.com/source?id=1"
    assert '"canonical_url":"https://example.com/news/1"' in metadata_evidence.text
    assert '"dc_date":"2026-08"' in metadata_evidence.text


@pytest.mark.asyncio
async def test_generic_web_does_not_emit_metadata_observation_for_plain_html(tmp_path: Path):
    repository = SQLiteRepository(tmp_path / "plain-page.sqlite")
    await repository.initialize()
    adapter = GenericWebAdapter(
        FastStub(),  # type: ignore[arg-type]
        BrowserStub(),  # type: ignore[arg-type]
        SnapshotService(repository),
    )
    request = CollectionRequest(
        consumer="page-metadata-test",
        analysis_id="analysis-plain-page",
        territory={"city": "Ижевск"},
        intents=["public_mentions"],
    )
    task = SourceTask(
        source_id="generic_web",
        goal="public_mentions",
        url="https://example.com/plain",
        metadata={"collection_id": "page-metadata-c2"},
    )
    fetched = SimpleNamespace(
        blocked=False,
        final_url="https://example.com/plain",
        text="<html><body>Plain factual page</body></html>",
        content_type="text/html",
        title=None,
        runtime="fast:test",
        status_code=200,
        metadata={},
        links=[],
    )

    result = await adapter.extract(task, fetched, request)

    assert [item.source_kind for item in result.observations] == ["web_page"]
    assert [item.type for item in result.evidence] == ["document"]
