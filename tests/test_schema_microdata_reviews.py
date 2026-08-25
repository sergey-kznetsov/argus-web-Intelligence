from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.contracts.models import CollectionRequest
from argus.extraction.pdf import BoundedPdfExtractor
from argus.history.snapshots import SnapshotService
from argus.sources.base import SourceTask
from argus.sources.schema_web import SchemaAwareSemanticWebAdapter
from argus.storage.sqlite import SQLiteRepository


class FastStub:
    pass


class BrowserStub:
    pass


def pdf_extractor() -> BoundedPdfExtractor:
    return BoundedPdfExtractor(
        max_bytes=1_000_000,
        max_pages=10,
        max_text_chars=100_000,
        timeout_seconds=1.0,
        memory_mb=128,
    )


def request() -> CollectionRequest:
    return CollectionRequest(
        consumer="schema-microdata-review-test",
        analysis_id="schema-microdata-review-analysis",
        territory={"city": "Ижевск"},
        intents=["reviews"],
    )


def fetched(html: str):
    return SimpleNamespace(
        blocked=False,
        final_url="https://example.com/maps/place/1",
        text=html,
        content_type="text/html; charset=utf-8",
        runtime="fast",
        status_code=200,
        metadata={},
        title="Public map card",
        links=[],
        body=html.encode("utf-8"),
    )


@pytest.mark.asyncio
async def test_nested_microdata_review_rating_author_and_subject_are_normalized(tmp_path: Path):
    repository = SQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    adapter = SchemaAwareSemanticWebAdapter(
        fast=FastStub(),
        browser=BrowserStub(),
        snapshots=SnapshotService(repository),
        pdf_extractor=pdf_extractor(),
    )
    task = SourceTask(
        source_id="generic_web",
        goal="reviews",
        url="https://example.com/maps/place/1",
        metadata={"collection_id": "collection-schema-microdata-review"},
    )
    html = """
    <article itemscope itemtype="https://schema.org/Review" itemid="/reviews/42">
      <meta itemprop="name" content="Хорошее место" />
      <p itemprop="reviewBody">Тихо и удобно.</p>
      <time itemprop="datePublished" datetime="2026-08-21T13:15:00+04:00"></time>
      <div itemprop="reviewRating" itemscope itemtype="https://schema.org/Rating">
        <meta itemprop="ratingValue" content="4.8" />
        <meta itemprop="bestRating" content="5" />
        <meta itemprop="worstRating" content="1" />
      </div>
      <div itemprop="author" itemscope itemtype="https://schema.org/Person" itemid="/people/7">
        <meta itemprop="name" content="Анна" />
      </div>
      <div itemprop="itemReviewed" itemscope itemtype="https://schema.org/LocalBusiness" itemid="/places/1">
        <meta itemprop="name" content="Кофейня Север" />
        <a itemprop="url" href="/places/1">Карточка</a>
      </div>
    </article>
    """

    result = await adapter.extract(task, fetched(html), request())

    review = next(
        item
        for item in result.observations
        if item.source_kind == "microdata" and item.entity_type == "review"
    )
    assert review.title == "Хорошее место"
    assert review.text == "Тихо и удобно."
    assert review.published_at is not None
    assert review.published_at.isoformat() == "2026-08-21T13:15:00+04:00"
    assert review.provenance["schema_review_normalization"] == {
        "source_declared_only": True,
        "rating": {"value": 4.8, "best": 5.0, "worst": 1.0, "valid": True},
        "author": "Анна",
        "item_reviewed": {
            "id": "https://example.com/places/1",
            "type": "https://schema.org/LocalBusiness",
            "name": "Кофейня Север",
            "url": "https://example.com/places/1",
        },
    }
    assert review.quality["schema_review_facts"] is True
    assert review.quality["schema_review_rating_valid"] is True

    raw_rating = review.data["properties"]["reviewRating"][0]
    assert raw_rating["properties"]["ratingValue"] == ["4.8"]
    raw_author = review.data["properties"]["author"][0]
    assert raw_author["properties"]["name"] == ["Анна"]

    evidence = next(item for item in result.evidence if item.observation_id == review.observation_id)
    assert evidence.metadata["schema_review_normalization"] == review.provenance[
        "schema_review_normalization"
    ]

    await repository.close()
