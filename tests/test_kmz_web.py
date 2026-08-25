from __future__ import annotations

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from argus.contracts.models import CollectionRequest
from argus.extraction.pdf import BoundedPdfExtractor
from argus.history.snapshots import SnapshotService
from argus.sources.base import SourceTask
from argus.sources.kmz_web import KmzAwareWebAdapter
from argus.storage.sqlite import SQLiteRepository


class FastStub:
    pass


class BrowserStub:
    async def fetch(self, url: str, *args, **kwargs):
        raise AssertionError(f"KMZ must not require browser fallback: {url}")


def make_kmz(entries: dict[str, bytes]) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as package:
        for name, body in entries.items():
            package.writestr(name, body)
    return buffer.getvalue()


def adapter(repository: SQLiteRepository) -> KmzAwareWebAdapter:
    return KmzAwareWebAdapter(
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
        consumer="kmz-test",
        analysis_id="analysis-kmz-1",
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


def fetched(url: str, body: bytes, content_type: str = "application/vnd.google-earth.kmz"):
    return SimpleNamespace(
        blocked=False,
        final_url=url,
        text="",
        content_type=content_type,
        runtime="fast",
        status_code=200,
        metadata={},
        title=None,
        links=[],
        body=body,
    )


@pytest.mark.asyncio
async def test_kmz_keeps_package_identity_and_reuses_kml_normalizer(tmp_path: Path):
    repository = SQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    web = adapter(repository)
    kml = b"""<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
      <Placemark id="kmz-point-1"><name>KMZ point</name>
        <Point><coordinates>37.6173,55.7558</coordinates></Point>
      </Placemark>
    </Document></kml>"""
    package = make_kmz({"doc.kml": kml, "images/icon.png": b"not-used"})
    url = "https://example.com/map.kmz"

    result = await web.extract(task(url, "collection-kmz-1"), fetched(url, package), request())

    dataset = next(item for item in result.observations if item.source_kind == "structured_data")
    point = next(item for item in result.observations if item.source_kind == "kml_point")
    evidence = next(item for item in result.evidence if item.observation_id == point.observation_id)

    assert dataset.quality["packaged_source"] is True
    assert dataset.data["container"]["format"] == "kmz"
    assert dataset.data["container"]["root_kml"] == "doc.kml"
    assert dataset.data["container"]["member_count"] == 2
    assert dataset.data["container"]["resources_resolved"] is False
    assert dataset.data["kml_summary"]["point_placemarks_extracted"] == 1

    assert point.entity_id == "kmz-point-1"
    assert point.geo is not None
    assert point.geo.longitude == pytest.approx(37.6173)
    assert point.geo.latitude == pytest.approx(55.7558)
    assert point.quality["packaged_source"] is True
    assert point.provenance["container"]["package_sha256"] == dataset.data["container"]["package_sha256"]
    assert point.provenance["container"]["root_kml_sha256"] == dataset.data["container"]["root_kml_sha256"]
    assert evidence.metadata["container"]["resources_resolved"] is False
    await repository.close()


@pytest.mark.asyncio
async def test_kmz_networklink_is_recorded_but_never_followed(tmp_path: Path):
    repository = SQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    web = adapter(repository)
    kml = b"""<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
      <NetworkLink><Link><href>https://127.0.0.1/internal.kml</href></Link></NetworkLink>
      <Placemark><Point><coordinates>53,56</coordinates></Point></Placemark>
    </Document></kml>"""
    package = make_kmz({"doc.kml": kml})
    url = "https://example.com/network.kmz"

    result = await web.extract(task(url, "collection-kmz-network"), fetched(url, package), request())

    dataset = next(item for item in result.observations if item.source_kind == "structured_data")
    assert dataset.data["kml_summary"]["network_links_seen"] == 1
    assert dataset.data["kml_summary"]["network_links_followed"] == 0
    assert dataset.data["container"]["network_links_followed"] is False
    assert len([item for item in result.observations if item.source_kind == "kml_point"]) == 1
    await repository.close()


@pytest.mark.asyncio
async def test_invalid_kmz_stays_hash_backed_partial_evidence(tmp_path: Path):
    repository = SQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    web = adapter(repository)
    package = make_kmz({"not-doc.kml": b"<kml/>"})
    url = "https://example.com/bad.kmz"

    result = await web.extract(task(url, "collection-kmz-invalid"), fetched(url, package), request())

    assert result.partial is True
    assert [error.code for error in result.errors] == ["KMZ_DOC_KML_MISSING"]
    dataset = next(item for item in result.observations if item.source_kind == "structured_data")
    assert dataset.data["container"]["format"] == "kmz"
    assert dataset.data["container"]["package_sha256"]
    assert dataset.data["container"]["root_kml_sha256"] is None
    assert dataset.quality["packaged_source"] is True
    assert not any(item.source_kind == "kml_point" for item in result.observations)
    await repository.close()


@pytest.mark.asyncio
async def test_kmz_suffix_overrides_ambiguous_excel_mime(tmp_path: Path):
    repository = SQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    web = adapter(repository)
    kml = b"""<kml xmlns="http://www.opengis.net/kml/2.2">
      <Placemark><Point><coordinates>30,60</coordinates></Point></Placemark>
    </kml>"""
    package = make_kmz({"doc.kml": kml})
    url = "https://example.com/map.kmz"

    result = await web.extract(
        task(url, "collection-kmz-mime"),
        fetched(url, package, "application/vnd.ms-excel"),
        request(),
    )

    assert any(item.source_kind == "kml_point" for item in result.observations)
    assert not any(item.source_kind == "office_document_file" for item in result.observations)
    await repository.close()


@pytest.mark.asyncio
async def test_kmz_health_exposes_bounded_package_contract(tmp_path: Path):
    repository = SQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    web = adapter(repository)

    health = await web.health()

    assert health["kmz_extraction"] is True
    assert health["kmz_root_kml"] == "doc.kml"
    assert health["kmz_resources_resolved"] is False
    assert health["kml_network_links_followed"] is False
    await repository.close()
