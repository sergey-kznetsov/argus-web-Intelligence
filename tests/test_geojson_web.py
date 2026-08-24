from __future__ import annotations

import gzip
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.contracts.models import CollectionRequest
from argus.extraction.pdf import BoundedPdfExtractor
from argus.history.snapshots import SnapshotService
from argus.sources.base import SourceTask
from argus.sources.geojson_web import GeoJsonAwareWebAdapter
from argus.storage.sqlite import SQLiteRepository


class FastStub:
    pass


class BrowserStub:
    async def fetch(self, url: str, *args, **kwargs):
        raise AssertionError(f"GeoJSON must not require browser fallback: {url}")


def adapter(repository: SQLiteRepository) -> GeoJsonAwareWebAdapter:
    return GeoJsonAwareWebAdapter(
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
        consumer="geojson-test",
        analysis_id="analysis-geojson-1",
        territory={"city": "Ижевск"},
        intents=["public_mentions"],
    )


def task(url: str, collection_id: str) -> SourceTask:
    return SourceTask(
        source_id="generic_web",
        goal="public_mentions",
        url=url,
        metadata={"collection_id": collection_id},
    )


def fetched(url: str, body: bytes, content_type: str):
    return SimpleNamespace(
        blocked=False,
        final_url=url,
        text=body.decode("utf-8", errors="replace"),
        content_type=content_type,
        runtime="fast",
        status_code=200,
        metadata={},
        title=None,
        links=[],
        body=body,
    )


@pytest.mark.asyncio
async def test_feature_collection_keeps_dataset_and_adds_only_valid_point_facts(tmp_path: Path):
    repository = SQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    web = adapter(repository)
    payload = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": "school-1",
                "geometry": {"type": "Point", "coordinates": [53.2045, 56.8526]},
                "properties": {
                    "name": "Школа",
                    "description": "Объект образования",
                },
            },
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[53.0, 56.0], [53.1, 56.0], [53.0, 56.1], [53.0, 56.0]]],
                },
                "properties": {"name": "Полигон"},
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [999, 999]},
                "properties": {"name": "Плохая точка"},
            },
            {
                "type": "Feature",
                "geometry": None,
                "properties": {"name": "Без геометрии"},
            },
        ],
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    url = "https://example.com/objects.geojson"

    result = await web.extract(
        task(url, "collection-geojson-1"),
        fetched(url, body, "application/geo+json"),
        request(),
    )

    dataset = next(item for item in result.observations if item.source_kind == "structured_data")
    points = [item for item in result.observations if item.source_kind == "geojson_point"]
    assert len(points) == 1
    point = points[0]

    assert dataset.data["payload"] == payload
    assert dataset.data["geojson_summary"] == {
        "root_type": "FeatureCollection",
        "features_seen": 4,
        "point_features_extracted": 1,
        "non_point_features_skipped": 1,
        "unlocated_features_skipped": 1,
        "invalid_features_skipped": 0,
        "invalid_points_skipped": 1,
        "axis_order": "longitude_latitude",
        "max_supported_dimensions": 3,
        "extractor": "geojson-point/1",
    }

    assert point.entity_type == "geospatial_feature"
    assert point.entity_id == "school-1"
    assert point.title == "Школа"
    assert point.text == "Объект образования"
    assert point.geo is not None
    assert point.geo.longitude == pytest.approx(53.2045)
    assert point.geo.latitude == pytest.approx(56.8526)
    assert point.data["coordinates"] == [53.2045, 56.8526]
    assert point.provenance["axis_order"] == "longitude_latitude"
    assert point.provenance["dataset_observation_id"] == dataset.observation_id
    assert point.provenance["snapshot_id"] == dataset.provenance["snapshot_id"]

    evidence = next(item for item in result.evidence if item.observation_id == point.observation_id)
    assert evidence.type == "geojson_point"
    assert evidence.source.url == url
    assert evidence.metadata["dataset_observation_id"] == dataset.observation_id
    assert evidence.metadata["axis_order"] == "longitude_latitude"
    await repository.close()


@pytest.mark.asyncio
async def test_explicit_geojson_signature_is_normalized_even_with_application_json(tmp_path: Path):
    repository = SQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    web = adapter(repository)
    payload = {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [37.6173, 55.7558, 150.0]},
        "properties": {"title": "Точка"},
    }
    body = json.dumps(payload).encode("utf-8")
    url = "https://example.com/data.json"

    result = await web.extract(
        task(url, "collection-geojson-json"),
        fetched(url, body, "application/json"),
        request(),
    )

    point = next(item for item in result.observations if item.source_kind == "geojson_point")
    assert point.geo is not None
    assert point.geo.longitude == pytest.approx(37.6173)
    assert point.geo.latitude == pytest.approx(55.7558)
    assert point.data["coordinate_dimensions"] == 3
    assert point.data["coordinates"] == [37.6173, 55.7558, 150.0]
    await repository.close()


@pytest.mark.asyncio
async def test_string_coordinates_are_not_reinterpreted_as_geojson_numbers(tmp_path: Path):
    repository = SQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    web = adapter(repository)
    payload = {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": ["53.2045", "56.8526"]},
        "properties": {"name": "String coordinates"},
    }
    body = json.dumps(payload).encode("utf-8")
    url = "https://example.com/data.geojson"

    result = await web.extract(
        task(url, "collection-geojson-string-coords"),
        fetched(url, body, "application/geo+json"),
        request(),
    )

    assert not any(item.source_kind == "geojson_point" for item in result.observations)
    dataset = next(item for item in result.observations if item.source_kind == "structured_data")
    assert dataset.data["geojson_summary"]["invalid_points_skipped"] == 1
    await repository.close()


@pytest.mark.asyncio
async def test_over_dimensional_position_is_not_silently_flattened(tmp_path: Path):
    repository = SQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    web = adapter(repository)
    payload = {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [53.2045, 56.8526, 100.0, 999.0]},
        "properties": {"name": "Four dimensions"},
    }
    body = json.dumps(payload).encode("utf-8")
    url = "https://example.com/data.geojson"

    result = await web.extract(
        task(url, "collection-geojson-four-dim"),
        fetched(url, body, "application/geo+json"),
        request(),
    )

    assert not any(item.source_kind == "geojson_point" for item in result.observations)
    dataset = next(item for item in result.observations if item.source_kind == "structured_data")
    assert dataset.data["geojson_summary"]["invalid_points_skipped"] == 1
    assert dataset.data["payload"] == payload
    await repository.close()


@pytest.mark.asyncio
async def test_geojson_gzip_flows_through_same_point_normalizer(tmp_path: Path):
    repository = SQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    web = adapter(repository)
    payload = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": 7,
                "geometry": {"type": "Point", "coordinates": [30.3141, 59.9386]},
                "properties": {"name": "Точка из gzip"},
            }
        ],
    }
    uncompressed = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    compressed = gzip.compress(uncompressed)
    url = "https://example.com/data.geojson.gz"
    response = SimpleNamespace(
        blocked=False,
        final_url=url,
        text="",
        content_type="application/gzip",
        runtime="fast",
        status_code=200,
        metadata={},
        title=None,
        links=[],
        body=compressed,
    )

    result = await web.extract(
        task(url, "collection-geojson-gzip"),
        response,
        request(),
    )

    dataset = next(item for item in result.observations if item.source_kind == "structured_data")
    point = next(item for item in result.observations if item.source_kind == "geojson_point")
    assert dataset.quality["compressed_source"] is True
    assert dataset.data["compression"]["inner_url"].endswith("data.geojson")
    assert point.entity_id == "7"
    assert point.geo is not None
    assert point.geo.longitude == pytest.approx(30.3141)
    assert point.geo.latitude == pytest.approx(59.9386)
    assert point.provenance["snapshot_id"] == dataset.provenance["snapshot_id"]
    await repository.close()


@pytest.mark.asyncio
async def test_geojson_health_capability_is_exposed(tmp_path: Path):
    repository = SQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    web = adapter(repository)

    health = await web.health()

    assert health["geojson_point_normalization"] is True
    await repository.close()
