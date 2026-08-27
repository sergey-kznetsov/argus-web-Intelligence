from __future__ import annotations

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
        raise AssertionError(f"GeoJSON extraction must not require browser fallback: {url}")


class LaterAdapterWithIncompatiblePointHelper(KmlAwareWebAdapter):
    def _point(self):
        raise AssertionError("GeoJSON parser must not dynamically dispatch a format helper")


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_class", [KmlAwareWebAdapter, LaterAdapterWithIncompatiblePointHelper])
async def test_kml_aware_adapter_keeps_geojson_point_parser_isolated(
    tmp_path: Path,
    adapter_class,
):
    repository = SQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    adapter = adapter_class(
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
    request = CollectionRequest(
        consumer="geojson-through-kml-test",
        analysis_id="analysis-geojson-through-kml",
        territory={"city": "Ижевск"},
        intents=["public_mentions"],
    )
    url = "https://example.com/point.geojson"
    body = b'{"type":"Feature","id":"point-1","geometry":{"type":"Point","coordinates":[53.2045,56.8526]},"properties":{"name":"Point"}}'
    response = SimpleNamespace(
        blocked=False,
        final_url=url,
        text=body.decode("utf-8"),
        content_type="application/geo+json",
        runtime="fast",
        status_code=200,
        metadata={},
        title=None,
        links=[],
        body=body,
    )
    task = SourceTask(
        source_id="generic_web",
        goal="public_mentions",
        url=url,
        metadata={"collection_id": "collection-geojson-through-kml"},
    )

    result = await adapter.extract(task, response, request)

    point = next(item for item in result.observations if item.source_kind == "geojson_point")
    assert point.entity_id == "point-1"
    assert point.geo is not None
    assert point.geo.longitude == pytest.approx(53.2045)
    assert point.geo.latitude == pytest.approx(56.8526)
    assert not any(item.source_kind == "kml_point" for item in result.observations)
    await repository.close()
