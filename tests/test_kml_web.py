from __future__ import annotations

import gzip
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.contracts.models import CollectionRequest
from argus.extraction.pdf import BoundedPdfExtractor
from argus.history.snapshots import SnapshotService
from argus.sources.base import SourceTask
from argus.sources.kml_web import KmlAwareWebAdapter
from argus.storage.sqlite import SQLiteRepository


class FastStub:
    pass


class BrowserStub:
    async def fetch(self, url: str, *args, **kwargs):
        raise AssertionError(f"KML must not require browser fallback: {url}")


def adapter(repository: SQLiteRepository, *, max_placemarks: int = 100) -> KmlAwareWebAdapter:
    return KmlAwareWebAdapter(
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
        kml_max_placemarks=max_placemarks,
    )


def request() -> CollectionRequest:
    return CollectionRequest(
        consumer="kml-test",
        analysis_id="analysis-kml-1",
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


def fetched(url: str, body: bytes, content_type: str = "application/vnd.google-earth.kml+xml"):
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
async def test_kml_placemark_point_reuses_dataset_snapshot(tmp_path: Path):
    repository = SQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    web = adapter(repository)
    body = b"""<?xml version="1.0" encoding="UTF-8"?>
    <kml xmlns="http://www.opengis.net/kml/2.2">
      <Document>
        <Placemark id="school-1">
          <name>School</name>
          <description>Public school</description>
          <Point><coordinates>53.2045,56.8526,120.0</coordinates></Point>
        </Placemark>
      </Document>
    </kml>
    """
    url = "https://example.com/objects.kml"

    result = await web.extract(task(url, "collection-kml-1"), fetched(url, body), request())

    dataset = next(item for item in result.observations if item.source_kind == "structured_data")
    point = next(item for item in result.observations if item.source_kind == "kml_point")
    evidence = next(item for item in result.evidence if item.observation_id == point.observation_id)

    assert point.entity_type == "geospatial_feature"
    assert point.entity_id == "school-1"
    assert point.title == "School"
    assert point.text == "Public school"
    assert point.geo is not None
    assert point.geo.longitude == pytest.approx(53.2045)
    assert point.geo.latitude == pytest.approx(56.8526)
    assert point.data["coordinate_dimensions"] == 3
    assert point.data["altitude"] == pytest.approx(120.0)
    assert point.provenance["snapshot_id"] == dataset.provenance["snapshot_id"]
    assert point.provenance["dataset_observation_id"] == dataset.observation_id
    assert point.provenance["axis_order"] == "longitude_latitude"
    assert evidence.type == "kml_point"
    assert evidence.source.url == url
    assert evidence.metadata["dataset_observation_id"] == dataset.observation_id
    assert dataset.data["kml_summary"]["point_placemarks_extracted"] == 1
    assert dataset.data["kml_summary"]["network_links_followed"] == 0
    await repository.close()


@pytest.mark.asyncio
async def test_kml_networklink_and_unsupported_geometry_are_never_followed_or_centroided(tmp_path: Path):
    repository = SQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    web = adapter(repository)
    body = b"""<kml xmlns="http://www.opengis.net/kml/2.2">
      <Document>
        <NetworkLink><Link><href>https://127.0.0.1/private.kml</href></Link></NetworkLink>
        <Placemark><name>Area</name><Polygon><outerBoundaryIs/></Polygon></Placemark>
        <Placemark><name>Path</name><LineString><coordinates>53,56 54,57</coordinates></LineString></Placemark>
        <Placemark><name>No geometry</name></Placemark>
      </Document>
    </kml>"""
    url = "https://example.com/map.kml"

    result = await web.extract(task(url, "collection-kml-network"), fetched(url, body), request())

    assert not any(item.source_kind == "kml_point" for item in result.observations)
    dataset = next(item for item in result.observations if item.source_kind == "structured_data")
    summary = dataset.data["kml_summary"]
    assert summary["network_links_seen"] == 1
    assert summary["network_links_followed"] == 0
    assert summary["unsupported_geometry_placemarks_skipped"] == 2
    assert summary["unlocated_placemarks_skipped"] == 1
    await repository.close()


@pytest.mark.asyncio
async def test_kml_invalid_or_multi_tuple_point_coordinates_are_rejected(tmp_path: Path):
    repository = SQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    web = adapter(repository)
    body = b"""<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
      <Placemark><Point><coordinates>999,999</coordinates></Point></Placemark>
      <Placemark><Point><coordinates>53,56 54,57</coordinates></Point></Placemark>
      <Placemark><Point><coordinates>53,56,1,2</coordinates></Point></Placemark>
      <Placemark><Point><coordinates>nan,56</coordinates></Point></Placemark>
    </Document></kml>"""
    url = "https://example.com/bad.kml"

    result = await web.extract(task(url, "collection-kml-invalid"), fetched(url, body), request())

    assert not any(item.source_kind == "kml_point" for item in result.observations)
    dataset = next(item for item in result.observations if item.source_kind == "structured_data")
    assert dataset.data["kml_summary"]["invalid_points_skipped"] == 4
    await repository.close()


@pytest.mark.asyncio
async def test_kml_placemark_limit_is_explicitly_partial(tmp_path: Path):
    repository = SQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    web = adapter(repository, max_placemarks=1)
    body = b"""<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
      <Placemark><name>One</name><Point><coordinates>53,56</coordinates></Point></Placemark>
      <Placemark><name>Two</name><Point><coordinates>54,57</coordinates></Point></Placemark>
    </Document></kml>"""
    url = "https://example.com/limited.kml"

    result = await web.extract(task(url, "collection-kml-limit"), fetched(url, body), request())

    points = [item for item in result.observations if item.source_kind == "kml_point"]
    dataset = next(item for item in result.observations if item.source_kind == "structured_data")
    assert len(points) == 1
    assert dataset.data["kml_summary"]["placemarks_seen"] == 2
    assert dataset.data["kml_summary"]["truncated"] is True
    assert dataset.quality["partial"] is True
    await repository.close()


@pytest.mark.asyncio
async def test_kml_gzip_flows_through_same_normalizer(tmp_path: Path):
    repository = SQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    web = adapter(repository)
    kml = b"""<kml xmlns="http://www.opengis.net/kml/2.2"><Placemark id="gz-1">
      <name>Compressed</name><Point><coordinates>30.3141,59.9386</coordinates></Point>
    </Placemark></kml>"""
    compressed = gzip.compress(kml)
    url = "https://example.com/map.kml.gz"
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

    result = await web.extract(task(url, "collection-kml-gzip"), response, request())

    dataset = next(item for item in result.observations if item.source_kind == "structured_data")
    point = next(item for item in result.observations if item.source_kind == "kml_point")
    assert dataset.quality["compressed_source"] is True
    assert dataset.data["compression"]["inner_url"].endswith("map.kml")
    assert point.entity_id == "gz-1"
    assert point.geo is not None
    assert point.geo.longitude == pytest.approx(30.3141)
    assert point.geo.latitude == pytest.approx(59.9386)
    await repository.close()


@pytest.mark.asyncio
async def test_kml_health_capability_is_exposed(tmp_path: Path):
    repository = SQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    web = adapter(repository, max_placemarks=17)

    health = await web.health()

    assert health["kml_point_normalization"] is True
    assert health["kml_network_links_followed"] is False
    assert health["kml_max_placemarks"] == 17
    await repository.close()
