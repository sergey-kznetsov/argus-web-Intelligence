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
            {"@id":"https://example.com/review/1","@type":"Review","name":"Отзыв"},
            {"@id":"https://example.com/news/1","@type":"NewsArticle","headline":"Новость"}
          ]
        }
      </script>
    </head><body>
      <div itemscope itemtype="https://schema.org/GovernmentOrganization" itemid="/org/1">
        <span itemprop="name">Администрация</span>
      </div>
    </body></html>
    """

    result = await adapter.extract(task, fetched(html), request())

    review = next(item for item in result.observations if item.source_kind == "json_ld" and item.title == "Отзыв")
    article = next(item for item in result.observations if item.source_kind == "json_ld" and item.title == "Новость")
    organization = next(item for item in result.observations if item.source_kind == "microdata")

    assert review.entity_type == "review"
    assert review.data["@type"] == "Review"
    assert review.provenance["schema_type_normalization"]["recognized_types"] == ["Review"]
    assert review.provenance["schema_type_normalization"]["context_hints"] == ["https://schema.org"]
    assert review.quality["schema_org_typed"] is True

    assert article.entity_type == "publication"
    assert article.data["@type"] == "NewsArticle"

    assert organization.entity_type == "organization"
    assert organization.data["item_types"] == ["https://schema.org/GovernmentOrganization"]
    assert organization.data["properties"]["name"] == ["Администрация"]

    for observation in (review, article, organization):
        linked = [item for item in result.evidence if item.observation_id == observation.observation_id]
        assert len(linked) == 1
        assert linked[0].metadata["schema_type_normalization"]["normalized_entity_type"] == observation.entity_type

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
      {"@context":"https://example.org/vocab/","@type":"Review","name":"Not schema.org"}
    </script>
    """

    result = await adapter.extract(task, fetched(html), request())

    observation = next(item for item in result.observations if item.source_kind == "json_ld")
    assert observation.entity_type == "structured_entity"
    assert observation.provenance["schema_type_normalization"]["recognized_types"] == []
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
    await repository.close()
