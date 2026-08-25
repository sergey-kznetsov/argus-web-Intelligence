from __future__ import annotations

from pathlib import Path

from argus.contracts.models import CollectionRecord, CollectionRequest, CollectionStatus, utcnow
from argus.crawler.models import FetchResult
from argus.extraction.images import extract_image_references
from argus.extraction.pdf import BoundedPdfExtractor
from argus.history.snapshots import SnapshotService
from argus.recipes.service import RecipeManager
from argus.sources.base import SourceTask
from argus.sources.historical_web import HistoricalTimelineWebAdapter
from argus.storage.lifecycle_sqlite import LifecycleAtomicSQLiteRepository


class UnusedRuntime:
    async def fetch(self, *args, **kwargs):
        raise AssertionError("runtime fetch is not used")


def test_image_extractor_collects_social_and_html_references_without_unsafe_urls():
    html = """
    <html><head>
      <meta property="og:image" content="/archive/cover.jpg">
      <meta name="twitter:image" content="javascript:alert(1)">
    </head><body>
      <figure>
        <img data-src="photos/street-1936.jpg" alt="Старая улица" title="Ижевск">
        <figcaption>Пушкинская улица, 1936 год</figcaption>
      </figure>
      <img src="https://user:secret@example.org/private.jpg">
    </body></html>
    """

    result = extract_image_references(
        html,
        content_type="text/html; charset=utf-8",
        base_url="https://archive.example.org/item/1",
    )

    assert [item.image_url for item in result.items] == [
        "https://archive.example.org/archive/cover.jpg",
        "https://archive.example.org/item/photos/street-1936.jpg",
    ]
    street = result.items[1]
    assert street.alt == "Старая улица"
    assert street.title == "Ижевск"
    assert street.caption == "Пушкинская улица, 1936 год"


def test_image_extractor_is_bounded_and_marks_truncation():
    html = "<html><body>" + "".join(
        f'<img src="/img/{index}.jpg" alt="{index}">' for index in range(10)
    ) + "</body></html>"

    result = extract_image_references(
        html,
        content_type="text/html",
        base_url="https://example.org/page",
        max_items=3,
    )

    assert len(result.items) == 3
    assert result.truncated is True


async def test_wayback_image_reference_keeps_archive_timestamp_and_page_evidence(tmp_path: Path):
    repository = LifecycleAtomicSQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    request = CollectionRequest(
        consumer="image-history-test",
        analysis_id="image-history-analysis",
        territory={"city": "Ижевск", "address": "Пушкинская, 277"},
        intents=["historical_context"],
        constraints={"max_pages": 20, "max_depth": 2},
    )
    ts = utcnow()
    collection_id = "image-history-collection"
    await repository.create_collection(
        CollectionRecord(
            collection_id=collection_id,
            request=request,
            status=CollectionStatus.RUNNING,
            created_at=ts,
            updated_at=ts,
        )
    )
    adapter = HistoricalTimelineWebAdapter(
        repository=repository,
        fast=UnusedRuntime(),
        browser=UnusedRuntime(),
        snapshots=SnapshotService(repository),
        recipes=RecipeManager(repository),
        agent=None,
        pdf_extractor=BoundedPdfExtractor(
            max_bytes=1_000_000,
            max_pages=10,
            max_text_chars=100_000,
            timeout_seconds=1.0,
            memory_mb=128,
        ),
    )
    timestamp = "19360102030405"
    original = "https://example.org/street"
    capture = f"https://web.archive.org/web/{timestamp}id_/{original}"
    task = SourceTask(
        source_id="generic_web",
        goal="historical_context",
        url=capture,
        metadata={
            "collection_id": collection_id,
            "archive_original_url": original,
            "archive_timestamp": timestamp,
            "discovery_provider": "wayback_cdx",
            "research_goals": ["historical_context"],
        },
    )
    fetched = FetchResult(
        url=capture,
        final_url=capture,
        status_code=200,
        content_type="text/html",
        text=(
            "<html><head><title>Archive</title></head><body><figure>"
            '<img src="https://cdn.example.org/1936.jpg" alt="Пушкинская улица">'
            "<figcaption>Ижевск, 1936</figcaption></figure></body></html>"
        ),
        title="Archive",
        runtime="fast",
    )

    result = await adapter.extract(task, fetched, request)
    image = next(item for item in result.observations if item.source_kind == "image_reference")
    evidence = next(item for item in result.evidence if item.observation_id == image.observation_id)

    assert image.url == "https://cdn.example.org/1936.jpg"
    assert image.data["caption"] == "Ижевск, 1936"
    assert image.provenance["archive"]["capture_timestamp"] == timestamp
    assert evidence.source.url == capture
    assert evidence.metadata["archive"]["original_url"] == original
    assert image.quality["image_binary_retrieved"] is False
    await repository.close()
