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


def adapter(repository: SQLiteRepository) -> SchemaAwareSemanticWebAdapter:
    return SchemaAwareSemanticWebAdapter(
        fast=FastStub(),
        browser=BrowserStub(),
        snapshots=SnapshotService(repository),
        pdf_extractor=BoundedPdfExtractor(
            max_bytes=1_000_000,
            max_pages=10,
            max_text_chars=100_000,
            timeout_seconds=1.0,
            memory_mb=128,
        ),
    )


def request() -> CollectionRequest:
    return CollectionRequest(
        consumer="schema-geo-test",
        analysis_id="analysis-schema-geo-1",
        territory={"city": "Ижевск"},
        intents=["public_mentions"],
    )


def task(collection_id: str) -> SourceTask:
    return SourceTask(
        source_id="generic_web",
        goal="public_mentions",
        url="https://example.com/place",
        metadata={"collection_id": collection_id},
    )


def fetched(html: str):
    return SimpleNamespace(
        blocked=False,
        final_url="https://example.com/place",
        text=html,
        content_type="text/html; charset=utf-8",
        runtime="fast",
        status_code=200,
        metadata={},
        title="Place",
        links=[],
        body=html.encode("utf-8"),
    )


@pytest.mark.asyncio
async def test_jsonld_place_nested_geo_populates_observation_point(tmp_path: Path):
    repository = SQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    web = adapter(repository)
    html = """
    <script type="application/ld+json">
      {
        "@context":"https://schema.org",
        "@type":"Place",
        "@id":"https://example.com/places/1",
        "name":"Объект",
        "geo": {
          "@type":"GeoCoordinates",
          "latitude":"56.8526",
          "longitude":"53.2045"
        }
      }
    </script>
    """

    result = await web.extract(task("collection-schema-geo-jsonld"), fetched(html), request())

    place = next(item for item in result.observations if item.source_kind == "json_ld")
    assert place.entity_type == "place"
    assert place.geo is not None
    assert place.geo.latitude == pytest.approx(56.8526)
    assert place.geo.longitude == pytest.approx(53.2045)
    assert place.provenance["schema_geo_normalization"] == {
        "source_field": "geo",
        "source_declared": True,
        "valid_point": True,
        "geocoding_used": False,
    }
    linked = next(item for item in result.evidence if item.observation_id == place.observation_id)
    assert linked.metadata["schema_geo_normalization"]["valid_point"] is True
    await repository.close()


@pytest.mark.asyncio
async def test_microdata_geocoordinates_entity_gets_source_declared_point(tmp_path: Path):
    repository = SQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    web = adapter(repository)
    html = """
    <div itemscope itemtype="https://schema.org/GeoCoordinates" itemid="/coords/1">
      <meta itemprop="latitude" content="55.7558" />
      <meta itemprop="longitude" content="37.6173" />
    </div>
    """

    result = await web.extract(task("collection-schema-geo-microdata"), fetched(html), request())

    coordinates = next(item for item in result.observations if item.source_kind == "microdata")
    assert coordinates.entity_type == "structured_entity"
    assert coordinates.provenance["schema_type_normalization"]["recognized_types"] == [
        "GeoCoordinates"
    ]
    assert coordinates.geo is not None
    assert coordinates.geo.latitude == pytest.approx(55.7558)
    assert coordinates.geo.longitude == pytest.approx(37.6173)
    assert coordinates.quality["geospatial_valid"] is True
    await repository.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [
        ("999", "37.0"),
        ("55.0", "999"),
        ("NaN", "37.0"),
        ("north", "east"),
    ],
)
async def test_invalid_schema_coordinates_are_visible_but_never_guessed(
    tmp_path: Path,
    latitude: str,
    longitude: str,
):
    repository = SQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    web = adapter(repository)
    html = f"""
    <script type="application/ld+json">
      {{
        "@context":"https://schema.org",
        "@type":"Place",
        "name":"Invalid point",
        "latitude":"{latitude}",
        "longitude":"{longitude}"
      }}
    </script>
    """

    result = await web.extract(task(f"collection-invalid-{latitude}-{longitude}"), fetched(html), request())

    place = next(item for item in result.observations if item.source_kind == "json_ld")
    assert place.geo is None
    assert place.provenance["schema_geo_normalization"]["source_declared"] is True
    assert place.provenance["schema_geo_normalization"]["valid_point"] is False
    assert place.provenance["schema_geo_normalization"]["geocoding_used"] is False
    assert place.quality["geospatial_valid"] is False
    await repository.close()


@pytest.mark.asyncio
async def test_schema_article_without_coordinates_does_not_get_geo_metadata(tmp_path: Path):
    repository = SQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    web = adapter(repository)
    html = """
    <script type="application/ld+json">
      {
        "@context":"https://schema.org",
        "@type":"NewsArticle",
        "headline":"Новость",
        "articleBody":"Текст"
      }
    </script>
    """

    result = await web.extract(task("collection-no-schema-geo"), fetched(html), request())

    article = next(item for item in result.observations if item.source_kind == "json_ld")
    assert article.entity_type == "publication"
    assert article.geo is None
    assert "schema_geo_normalization" not in article.provenance
    assert "geospatial_valid" not in article.quality
    await repository.close()
