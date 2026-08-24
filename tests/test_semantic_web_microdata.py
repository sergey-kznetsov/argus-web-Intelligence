from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.contracts.models import CollectionRequest
from argus.extraction.pdf import BoundedPdfExtractor
from argus.history.snapshots import SnapshotService
from argus.sources.base import SourceTask
from argus.sources.semantic_web import SemanticWebAdapter
from argus.storage.sqlite import SQLiteRepository


class FastStub:
    pass


class BrowserStub:
    pass


def pdf_extractor() -> BoundedPdfExtractor:
    return BoundedPdfExtractor(
        max_bytes=1024,
        max_pages=1,
        max_text_chars=1000,
        timeout_seconds=1,
        memory_mb=128,
    )


def adapter(repository: SQLiteRepository) -> SemanticWebAdapter:
    return SemanticWebAdapter(
        fast=FastStub(),
        browser=BrowserStub(),
        snapshots=SnapshotService(repository),
        pdf_extractor=pdf_extractor(),
    )


def request() -> CollectionRequest:
    return CollectionRequest(
        consumer="semantic-microdata-test",
        analysis_id="analysis-microdata-1",
        territory={"city": "Ижевск"},
        intents=["local_news"],
    )


def fetched(html: str):
    return SimpleNamespace(
        blocked=False,
        final_url="https://example.com/news/page",
        text=html,
        content_type="text/html; charset=utf-8",
        runtime="fast",
        status_code=200,
        metadata={},
        title="News page",
        links=[],
        body=html.encode("utf-8"),
    )


@pytest.mark.asyncio
async def test_microdata_reuses_page_snapshot_and_adds_structured_evidence(tmp_path: Path):
    repository = SQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    value = adapter(repository)
    task = SourceTask(
        source_id="generic_web",
        goal="local_news",
        url="https://example.com/news/page",
        metadata={
            "collection_id": "collection-microdata-1",
            "research_goals": ["local_news"],
        },
    )
    html = """
    <html><body>
      <article itemscope itemtype="https://schema.org/NewsArticle" itemid="/news/1">
        <meta itemprop="headline" content="Открылась новая школа" />
        <p itemprop="description">В районе открылась новая школа.</p>
        <time itemprop="datePublished" datetime="2026-08-20T10:00:00+04:00"></time>
        <a itemprop="url" href="/news/1">Источник</a>
      </article>
    </body></html>
    """

    result = await value.extract(task, fetched(html), request())

    page = next(item for item in result.observations if item.source_kind == "web_page")
    microdata = next(item for item in result.observations if item.source_kind == "microdata")
    evidence = next(item for item in result.evidence if item.type == "microdata")

    assert page.data["microdata_summary"] == {
        "items_seen": 1,
        "items_extracted": 1,
        "items_skipped": 0,
        "itemref_skipped": 0,
        "truncated": False,
        "extractor": "html-microdata-explicit/1",
    }
    assert microdata.entity_type == "structured_entity"
    assert microdata.entity_id == "https://example.com/news/1"
    assert microdata.url == "https://example.com/news/page"
    assert microdata.title == "Открылась новая школа"
    assert microdata.text == "В районе открылась новая школа."
    assert microdata.published_at is not None
    assert microdata.data["item_types"] == ["https://schema.org/NewsArticle"]
    assert microdata.data["properties"]["url"] == ["https://example.com/news/1"]
    assert microdata.provenance["snapshot_id"] == page.provenance["snapshot_id"]
    assert microdata.provenance["remote_vocabularies_resolved"] is False
    assert microdata.quality["lossless"] is True

    assert evidence.observation_id == microdata.observation_id
    assert evidence.source.url == "https://example.com/news/page"
    assert evidence.metadata["snapshot_id"] == page.provenance["snapshot_id"]
    assert evidence.metadata["remote_vocabularies_resolved"] is False

    snapshot = await repository.latest_snapshot("https://example.com/news/page")
    assert snapshot is not None
    assert snapshot.snapshot_id == page.provenance["snapshot_id"]
    await repository.close()


@pytest.mark.asyncio
async def test_itemref_skip_is_visible_in_page_summary_without_partial_fact(tmp_path: Path):
    repository = SQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    value = adapter(repository)
    task = SourceTask(
        source_id="generic_web",
        goal="local_news",
        url="https://example.com/news/page",
        metadata={"collection_id": "collection-microdata-2"},
    )
    html = """
    <div id="extra"><span itemprop="name">External</span></div>
    <div itemscope itemtype="https://schema.org/Thing" itemref="extra">
      <span itemprop="description">Local</span>
    </div>
    """

    result = await value.extract(task, fetched(html), request())

    assert not any(item.source_kind == "microdata" for item in result.observations)
    page = next(item for item in result.observations if item.source_kind == "web_page")
    assert page.data["microdata_summary"]["items_seen"] == 1
    assert page.data["microdata_summary"]["items_extracted"] == 0
    assert page.data["microdata_summary"]["itemref_skipped"] == 1
    await repository.close()


@pytest.mark.asyncio
async def test_semantic_web_health_exposes_microdata_capability(tmp_path: Path):
    repository = SQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    value = adapter(repository)

    health = await value.health()

    assert health["html_microdata_extraction"] is True
    await repository.close()
