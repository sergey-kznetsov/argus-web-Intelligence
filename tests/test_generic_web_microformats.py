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
async def test_generic_web_emits_h_entry_and_h_review_as_evidence_backed_facts(tmp_path: Path):
    repository = SQLiteRepository(tmp_path / "microformats.sqlite")
    await repository.initialize()
    adapter = GenericWebAdapter(
        FastStub(),  # type: ignore[arg-type]
        BrowserStub(),  # type: ignore[arg-type]
        SnapshotService(repository),
    )
    request = CollectionRequest(
        consumer="microformats-test",
        analysis_id="analysis-microformats",
        territory={"city": "Ижевск"},
        intents=["discussions", "reviews"],
    )
    task = SourceTask(
        source_id="generic_web",
        goal="discussions",
        url="https://example.com/thread",
        metadata={
            "collection_id": "microformats-c1",
            "research_goals": ["discussions", "reviews"],
        },
    )
    html = """
    <html><body>
      <article class="h-entry">
        <h2 class="p-name">Публичное обсуждение</h2>
        <a class="u-url" href="/posts/10">post</a>
        <time class="dt-published" datetime="2026-08-23T13:00:00+04:00"></time>
        <div class="e-content">Текст публичного сообщения</div>
      </article>
      <div class="h-review">
        <h3 class="p-name">Отзыв посетителя</h3>
        <span class="p-item">Объект</span>
        <data class="p-rating" value="4">4</data>
        <div class="e-content">Текст открытого отзыва</div>
        <a class="u-url" href="/reviews/5">review</a>
      </div>
    </body></html>
    """
    fetched = SimpleNamespace(
        blocked=False,
        final_url="https://example.com/thread",
        text=html,
        content_type="text/html",
        title="Thread",
        runtime="fast:test",
        status_code=200,
        metadata={},
        links=[],
    )

    result = await adapter.extract(task, fetched, request)

    by_kind = {item.source_kind: item for item in result.observations}
    assert "web_page" in by_kind
    assert "microformat_h_entry" in by_kind
    assert "microformat_h_review" in by_kind

    entry = by_kind["microformat_h_entry"]
    review = by_kind["microformat_h_review"]
    assert entry.entity_type == "publication"
    assert entry.url == "https://example.com/posts/10"
    assert entry.title == "Публичное обсуждение"
    assert entry.text == "Текст публичного сообщения"
    assert entry.published_at is not None
    assert entry.provenance["page_url"] == "https://example.com/thread"
    assert entry.provenance["explicit_properties_only"] is True

    assert review.entity_type == "review"
    assert review.url == "https://example.com/reviews/5"
    assert review.text == "Текст открытого отзыва"
    assert review.data["properties"]["p-rating"] == ["4"]
    assert review.quality["source_declared"] is True

    entry_evidence = next(
        item for item in result.evidence if item.type == "microformat_h_entry"
    )
    review_evidence = next(
        item for item in result.evidence if item.type == "microformat_h_review"
    )
    assert entry_evidence.source.url == "https://example.com/thread"
    assert review_evidence.source.url == "https://example.com/thread"
    assert entry_evidence.observation_id == entry.observation_id
    assert review_evidence.observation_id == review.observation_id
    assert '"p-rating":["4"]' in review_evidence.text

    page = by_kind["web_page"]
    assert page.data["microformats_summary"] == {
        "roots_seen": 2,
        "roots_skipped": 0,
        "items": 2,
        "truncated": False,
    }
    assert entry.provenance["snapshot_id"] == page.provenance["snapshot_id"]
    assert review.provenance["snapshot_id"] == page.provenance["snapshot_id"]
