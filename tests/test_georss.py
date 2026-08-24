from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.contracts.models import CollectionRequest
from argus.history.snapshots import SnapshotService
from argus.sources.base import SourceTask
from argus.sources.rss import RSSAdapter
from argus.storage.sqlite import SQLiteRepository


class FastStub:
    pass


def request() -> CollectionRequest:
    return CollectionRequest(
        consumer="georss-test",
        analysis_id="analysis-georss-1",
        territory={"city": "Ижевск"},
        intents=["incidents"],
    )


def task(collection_id: str) -> SourceTask:
    return SourceTask(
        source_id="rss_atom",
        goal="incidents",
        url="https://example.com/feed.xml",
        metadata={"collection_id": collection_id},
    )


def fetched(text: str):
    return SimpleNamespace(
        blocked=False,
        final_url="https://example.com/feed.xml",
        text=text,
        content_type="application/rss+xml",
    )


@pytest.mark.asyncio
async def test_georss_simple_point_populates_observation_geo_without_geocoding(tmp_path: Path):
    repo = SQLiteRepository(tmp_path / "db.sqlite")
    await repo.initialize()
    adapter = RSSAdapter(FastStub(), SnapshotService(repo))
    xml = """
    <rss xmlns:georss="http://www.georss.org/georss/">
      <channel><item>
        <title>Incident</title>
        <link>https://example.com/incidents/1</link>
        <georss:point>56.8526 53.2045</georss:point>
      </item></channel>
    </rss>
    """

    result = await adapter.extract(task("collection-georss-simple"), fetched(xml), request())

    observation = result.observations[0]
    assert observation.geo is not None
    assert observation.geo.latitude == pytest.approx(56.8526)
    assert observation.geo.longitude == pytest.approx(53.2045)
    assert observation.data["geospatial"] == {
        "declared": True,
        "valid_point": True,
        "format": "georss_simple_point",
    }
    assert observation.provenance["geospatial"] == {
        "source_declared": True,
        "format": "georss_simple_point",
        "geocoding_used": False,
    }
    assert observation.quality["geospatial_valid"] is True
    await repo.close()


@pytest.mark.asyncio
async def test_georss_gml_point_is_supported_conservatively(tmp_path: Path):
    repo = SQLiteRepository(tmp_path / "db.sqlite")
    await repo.initialize()
    adapter = RSSAdapter(FastStub(), SnapshotService(repo))
    xml = """
    <rss xmlns:georss="http://www.georss.org/georss/"
         xmlns:gml="http://www.opengis.net/gml">
      <channel><item>
        <title>Location</title>
        <georss:where><gml:Point><gml:pos>55.7558 37.6173</gml:pos></gml:Point></georss:where>
      </item></channel>
    </rss>
    """

    result = await adapter.extract(task("collection-georss-gml"), fetched(xml), request())

    observation = result.observations[0]
    assert observation.geo is not None
    assert observation.geo.latitude == pytest.approx(55.7558)
    assert observation.geo.longitude == pytest.approx(37.6173)
    assert observation.data["geospatial"]["format"] == "georss_gml_point"
    assert result.evidence[0].metadata["geospatial_format"] == "georss_gml_point"
    await repo.close()


@pytest.mark.asyncio
async def test_invalid_declared_georss_is_visible_but_never_guessed(tmp_path: Path):
    repo = SQLiteRepository(tmp_path / "db.sqlite")
    await repo.initialize()
    adapter = RSSAdapter(FastStub(), SnapshotService(repo))
    xml = """
    <rss xmlns:georss="http://www.georss.org/georss/">
      <channel><item>
        <title>Bad location</title>
        <georss:point>999 999</georss:point>
      </item></channel>
    </rss>
    """

    result = await adapter.extract(task("collection-georss-invalid"), fetched(xml), request())

    observation = result.observations[0]
    assert observation.geo is None
    assert observation.data["geospatial"]["declared"] is True
    assert observation.data["geospatial"]["valid_point"] is False
    assert observation.quality["geospatial_valid"] is False
    assert observation.provenance["geospatial"]["geocoding_used"] is False
    await repo.close()


@pytest.mark.asyncio
async def test_unrelated_point_element_is_not_treated_as_georss(tmp_path: Path):
    repo = SQLiteRepository(tmp_path / "db.sqlite")
    await repo.initialize()
    adapter = RSSAdapter(FastStub(), SnapshotService(repo))
    xml = """
    <rss><channel><item>
      <title>Plain point tag</title>
      <point>56.0 53.0</point>
    </item></channel></rss>
    """

    result = await adapter.extract(task("collection-georss-unrelated"), fetched(xml), request())

    observation = result.observations[0]
    assert observation.geo is None
    assert observation.data["geospatial"] == {
        "declared": False,
        "valid_point": False,
        "format": None,
    }
    assert observation.quality["geospatial_valid"] is True
    await repo.close()
