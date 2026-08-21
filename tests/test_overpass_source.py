from pathlib import Path

import pytest

from argus.contracts.models import CollectionRequest, StructuredError
from argus.history.snapshots import SnapshotService
from argus.maps.contracts import MapPlace, MapSearchResult
from argus.sources.overpass_map import OverpassSourceAdapter
from argus.storage.sqlite import SQLiteRepository


class FakeOverpassProvider:
    provider_id = "openstreetmap_overpass"
    endpoint = "https://overpass.example/api/interpreter"

    def __init__(self, result: MapSearchResult) -> None:
        self.result = result
        self.requests = []

    async def search(self, request):
        self.requests.append(request)
        return self.result

    async def health(self):
        return {"provider": self.provider_id, "status": "ok"}


def request():
    return CollectionRequest(
        consumer="test",
        analysis_id="analysis-1",
        territory={
            "city": "Ижевск",
            "point": {"latitude": 56.85, "longitude": 53.2},
            "radius_meters": 1000,
        },
        intents=["school"],
    )


def place():
    return MapPlace(
        provider="openstreetmap_overpass",
        provider_place_id="node/42",
        name="Школа 1",
        address="Ижевск, Пушкинская 1",
        point={"latitude": 56.851, "longitude": 53.201},
        categories=["amenity:school"],
        source_url="https://www.openstreetmap.org/node/42",
        attributes={"osm_tags": {"amenity": "school", "name": "Школа 1"}},
        provenance={
            "retrieval": "overpass",
            "attribution": "© OpenStreetMap contributors",
            "data_license": "ODbL",
        },
    )


@pytest.mark.asyncio
async def test_overpass_source_creates_observation_evidence_and_snapshot(tmp_path: Path):
    repo = SQLiteRepository(tmp_path / "db.sqlite")
    await repo.initialize()
    provider = FakeOverpassProvider(
        MapSearchResult(provider="openstreetmap_overpass", places=[place()])
    )
    adapter = OverpassSourceAdapter(provider, SnapshotService(repo))
    tasks = await adapter.discover(request())
    assert len(tasks) == 1
    tasks[0].metadata["collection_id"] = "collection-1"

    fetched = await adapter.fetch(tasks[0])
    result = await adapter.extract(tasks[0], fetched, request())
    assert len(result.observations) == 1
    assert len(result.evidence) == 1
    observation = result.observations[0]
    assert observation.source_kind == "map_place"
    assert observation.geo.latitude == 56.851
    assert observation.url == "https://www.openstreetmap.org/node/42"
    assert observation.provenance["data_license"] == "ODbL"
    assert observation.provenance["snapshot_id"]
    assert result.evidence[0].source.url == observation.url

    snapshot = await repo.latest_snapshot(observation.url)
    assert snapshot is not None
    assert snapshot.snapshot_id == observation.provenance["snapshot_id"]


@pytest.mark.asyncio
async def test_overpass_source_identity_is_stable_for_same_collection_and_facts(tmp_path: Path):
    repo = SQLiteRepository(tmp_path / "db.sqlite")
    await repo.initialize()
    provider = FakeOverpassProvider(
        MapSearchResult(provider="openstreetmap_overpass", places=[place()])
    )
    adapter = OverpassSourceAdapter(provider, SnapshotService(repo))
    task = (await adapter.discover(request()))[0]
    task.metadata["collection_id"] = "collection-1"

    first = await adapter.extract(task, await adapter.fetch(task), request())
    second = await adapter.extract(task, await adapter.fetch(task), request())
    assert first.observations[0].observation_id == second.observations[0].observation_id
    assert first.evidence[0].evidence_id == second.evidence[0].evidence_id
    assert first.observations[0].provenance["snapshot_id"] != second.observations[0].provenance[
        "snapshot_id"
    ]


@pytest.mark.asyncio
async def test_overpass_source_preserves_provider_errors(tmp_path: Path):
    repo = SQLiteRepository(tmp_path / "db.sqlite")
    await repo.initialize()
    error = StructuredError(
        code="MAP_PROVIDER_BLOCKED",
        message="rate limited",
        retryable=True,
        source_id="map:openstreetmap_overpass",
    )
    provider = FakeOverpassProvider(
        MapSearchResult(
            provider="openstreetmap_overpass",
            blocked=True,
            errors=[error],
        )
    )
    adapter = OverpassSourceAdapter(provider, SnapshotService(repo))
    task = (await adapter.discover(request()))[0]
    task.metadata["collection_id"] = "collection-1"
    result = await adapter.extract(task, await adapter.fetch(task), request())
    assert result.blocked is True
    assert result.errors[0].code == "MAP_PROVIDER_BLOCKED"
