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
        consumer="schema-test",
        analysis_id="analysis-schema-1",
        territory={"city": "Ижевск"},
        intents=["public_mentions"],
    )


def fetched(html: str):
    return SimpleNamespace(
        blocked=False,
        final_url="https://example.com/page",
        text=html,
        content_type="text/html; charset=utf-8",
        runtime="fast",
        status_code=200,
        metadata={},
        title="Structured page",
        links=[],
        body=html.encode("utf-8"),
    )


@pytest.mark.asyncio
async def test_jsonld_and_microdata_receive_conservative_schema_entity_types(tmp_path: Path):
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
        goal="public_mentions",
        url="https://example.com/page",
        metadata={"collection_id": "collection-schema-1"},
    )
    html = """
    <html><head>
      <script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@graph": [
            {
              "@id":"https://example.com/review/1",
              "@type":"Review",
              "name":"Отзыв",
              "description":"Короткое описание",
              "reviewBody":"Полный текст отзыва",
              "datePublished":"2026-08-19T12:30:00+04:00",
              "reviewRating":{
                "@type":"Rating",
                "ratingValue":"4.7",
                "bestRating":"5",
                "worstRating":"1"
              },
              "author":{"@type":"Person","name":"Иван"},
              "itemReviewed":{
                "@type":"CafeOrCoffeeShop",
                "@id":"https://example.com/place/1",
                "name":"Кофейня Север",
                "url":"https://example.com/place/1"
              }
            },
            {
              "@id":"https://example.com/news/1",
              "@type":"NewsArticle",
              "headline":"Новость",
              "description":"Краткая новость",
              "articleBody":"Полный текст новости",
              "datePublished":"2026-08-20T10:00:00+04:00"
            }
          ]
        }
      </script>
    </head><body>
      <div itemscope itemtype="https://schema.org/GovernmentOrganization" itemid="/org/1">
        <span itemprop="name">Администрация</span>
        <span itemprop="description">Муниципальный орган</span>
      </div>
    </body></html>
    """

    result = await adapter.extract(task, fetched(html), request())

    review = next(
        item
        for item in result.observations
        if item.source_kind == "json_ld" and item.title == "Отзыв"
    )
    article = next(
        item
        for item in result.observations
        if item.source_kind == "json_ld" and item.title == "Новость"
    )
    organization = next(item for item in result.observations if item.source_kind == "microdata")

    assert review.entity_type == "review"
    assert review.data["@type"] == "Review"
    assert review.text == "Полный текст отзыва"
    assert review.published_at is not None
    assert review.published_at.isoformat() == "2026-08-19T12:30:00+04:00"
    assert review.provenance["schema_type_normalization"]["recognized_types"] == ["Review"]
    assert review.provenance["schema_type_normalization"]["context_hints"] == [
        "https://schema.org"
    ]
    assert review.provenance["schema_field_normalization"] == {
        "text_field": "reviewBody",
        "published_at_field": "datePublished",
        "source_declared_only": True,
    }
    assert review.provenance["schema_review_normalization"] == {
        "source_declared_only": True,
        "rating": {"value": 4.7, "best": 5.0, "worst": 1.0, "valid": True},
        "author": "Иван",
        "item_reviewed": {
            "name": "Кофейня Север",
            "id": "https://example.com/place/1",
            "url": "https://example.com/place/1",
        },
    }
    assert review.quality["schema_org_typed"] is True
    assert review.quality["schema_review_facts"] is True
    assert review.quality["schema_review_rating_valid"] is True

    assert article.entity_type == "publication"
    assert article.data["@type"] == "NewsArticle"
    assert article.text == "Полный текст новости"
    assert article.published_at is not None
    assert article.published_at.isoformat() == "2026-08-20T10:00:00+04:00"
    assert article.provenance["schema_field_normalization"]["text_field"] == "articleBody"

    assert organization.entity_type == "organization"
    assert organization.data["item_types"] == ["https://schema.org/GovernmentOrganization"]
    assert organization.data["properties"]["name"] == ["Администрация"]
    assert organization.text == "Муниципальный орган"
    assert organization.provenance["schema_field_normalization"]["text_field"] == "description"

    for observation in (review, article, organization):
        linked = [
            item for item in result.evidence if item.observation_id == observation.observation_id
        ]
        assert len(linked) == 1
        evidence = linked[0]
        assert (
            evidence.metadata["schema_type_normalization"]["normalized_entity_type"]
            == observation.entity_type
        )
        assert evidence.metadata["schema_field_normalization"] == observation.provenance[
            "schema_field_normalization"
        ]
    review_evidence = next(
        item for item in result.evidence if item.observation_id == review.observation_id
    )
    assert review_evidence.metadata["schema_review_normalization"] == review.provenance[
        "schema_review_normalization"
    ]

    await repository.close()


@pytest.mark.asyncio
async def test_invalid_schema_review_rating_is_retained_raw_but_not_marked_valid(tmp_path: Path):
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
        url="https://example.com/page",
        metadata={"collection_id": "collection-schema-invalid-rating"},
    )
    html = """
    <script type="application/ld+json">
      {
        "@context":"https://schema.org",
        "@type":"Review",
        "name":"Некорректная шкала",
        "reviewBody":"Источник объявил рейтинг вне шкалы",
        "reviewRating":{"ratingValue":"7","bestRating":"5","worstRating":"1"}
      }
    </script>
    """

    result = await adapter.extract(task, fetched(html), request())

    review = next(item for item in result.observations if item.entity_type == "review")
    assert review.data["reviewRating"]["ratingValue"] == "7"
    assert review.provenance["schema_review_normalization"]["rating"] == {
        "value": 7.0,
        "best": 5.0,
        "worst": 1.0,
        "valid": False,
    }
    assert review.quality["schema_review_rating_valid"] is False
    await repository.close()


@pytest.mark.asyncio
async def test_unknown_jsonld_vocabulary_remains_generic_structured_entity(tmp_path: Path):
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
        goal="public_mentions",
        url="https://example.com/page",
        metadata={"collection_id": "collection-schema-2"},
    )
    html = """
    <script type="application/ld+json">
      {
        "@context":"https://example.org/vocab/",
        "@type":"Review",
        "name":"Not schema.org",
        "reviewBody":"Must not become schema-normalized text",
        "datePublished":"2026-08-19T12:30:00+04:00"
      }
    </script>
    """

    result = await adapter.extract(task, fetched(html), request())

    observation = next(item for item in result.observations if item.source_kind == "json_ld")
    assert observation.entity_type == "structured_entity"
    assert observation.provenance["schema_type_normalization"]["recognized_types"] == []
    assert "schema_field_normalization" not in observation.provenance
    assert "schema_review_normalization" not in observation.provenance
    assert observation.text is None
    assert observation.published_at is None
    assert observation.quality["schema_org_typed"] is False
    await repository.close()


@pytest.mark.asyncio
async def test_schema_aware_health_capability_is_exposed(tmp_path: Path):
    repository = SQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    adapter = SchemaAwareSemanticWebAdapter(
        fast=FastStub(),
        browser=BrowserStub(),
        snapshots=SnapshotService(repository),
        pdf_extractor=pdf_extractor(),
    )

    health = await adapter.health()

    assert health["schema_org_type_normalization"] is True
    assert health["schema_org_field_normalization"] is True
    assert health["schema_org_review_normalization"] is True
    await repository.close()
