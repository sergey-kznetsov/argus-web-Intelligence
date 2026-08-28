from __future__ import annotations

import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from argus.contracts.models import CollectionRequest, Point
from argus.crawler.models import FetchResult
from argus.research.intent_coverage import IntentCoverageEvaluator
from argus.sources.pastvu import PastVuHistoricalAdapter


class _Fast:
    def __init__(self, result: FetchResult | None = None) -> None:
        self.result = result
        self.urls: list[str] = []

    async def fetch(self, url: str) -> FetchResult:
        self.urls.append(url)
        if self.result is None:
            raise AssertionError("fetch result was not configured")
        return self.result


class _Snapshots:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def capture(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return SimpleNamespace(snapshot_id="pastvu-snapshot-1")


class _Geocoder:
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def search(self, query: str, *, limit: int = 3, language: str | None = None):
        self.queries.append(query)
        candidate = SimpleNamespace(
            provider="test-geocoder",
            provider_place_id="place-1",
            display_name="Комсомольский проспект, 27, Пермь",
            point=Point(latitude=58.009636, longitude=56.239332),
            source_url="https://example.org/geocode/place-1",
            importance=0.9,
            provenance={"retrieval": "test"},
        )
        return SimpleNamespace(
            provider="test-geocoder",
            blocked=False,
            errors=[],
            candidates=[candidate],
        )

    async def health(self):
        return {"provider": "test-geocoder", "status": "ok"}


def _request(
    *,
    point: Point | None = None,
    intents: list[str] | None = None,
    radius_meters: int | None = None,
) -> CollectionRequest:
    return CollectionRequest(
        consumer="historical-test",
        analysis_id="historical-test",
        territory={
            "city": "Пермь",
            "address": "Комсомольский проспект, 27",
            "point": point.model_dump(mode="json") if point else None,
            "radius_meters": radius_meters,
        },
        intents=intents or ["historical_images"],
    )


def _response(payload: object) -> FetchResult:
    text = json.dumps(payload, ensure_ascii=False)
    return FetchResult(
        url="https://api.pastvu.com/api2",
        final_url="https://api.pastvu.com/api2",
        status_code=200,
        content_type="application/json",
        text=text,
        body=text.encode("utf-8"),
    )


def _photo_payload(**overrides: object) -> dict[str, object]:
    photo: dict[str, object] = {
        "cid": 123456,
        "title": "Комсомольский проспект",
        "year": 1964,
        "year2": 1965,
        "geo": [58.0097, 56.2394],
        "distance": None,
        "file": "00/11/22/example.jpg",
    }
    photo.update(overrides)
    return {"result": {"photos": [photo]}}


@pytest.mark.asyncio
async def test_discover_uses_official_nearest_photo_api_with_existing_point():
    adapter = PastVuHistoricalAdapter(_Fast(), _Snapshots())
    request = _request(point=Point(latitude=58.009636, longitude=56.239332))

    tasks = await adapter.discover(request)

    assert len(tasks) == 1
    task = tasks[0]
    assert task.source_id == "pastvu_historical"
    assert task.goal == "historical_images"
    parsed = urlsplit(task.url)
    assert parsed.scheme == "https"
    assert parsed.hostname == "api.pastvu.com"
    assert parsed.path == "/api2"
    query = parse_qs(parsed.query)
    assert query["method"] == ["photo.giveNearestPhotos"]
    params = json.loads(query["params"][0])
    assert params["geo"] == [58.009636, 56.239332]
    assert params["distance"] == 1000
    assert params["type"] == "photo"
    assert params["limit"] == 20
    assert isinstance(params["year2"], int)
    assert task.metadata["source_declared_historical_media"] is True
    assert task.metadata["research_goals"] == ["historical_images"]


@pytest.mark.asyncio
async def test_discover_geocodes_full_city_and_address_when_point_missing():
    geocoder = _Geocoder()
    adapter = PastVuHistoricalAdapter(_Fast(), _Snapshots(), geocoder=geocoder)

    tasks = await adapter.discover(_request())

    assert geocoder.queries == ["Пермь, Комсомольский проспект, 27"]
    assert len(tasks) == 1
    assert tasks[0].metadata["geocoding"]["navigation_only"] is True
    assert tasks[0].metadata["geocoding"]["geocoding_is_historical_evidence"] is False


@pytest.mark.asyncio
async def test_discover_without_point_or_geocoder_falls_back_cleanly():
    adapter = PastVuHistoricalAdapter(_Fast(), _Snapshots())

    assert await adapter.discover(_request()) == []


@pytest.mark.asyncio
async def test_context_only_request_can_use_source_declared_pastvu_metadata():
    adapter = PastVuHistoricalAdapter(_Fast(), _Snapshots())
    request = _request(
        point=Point(latitude=58.009636, longitude=56.239332),
        intents=["historical_context"],
    )

    tasks = await adapter.discover(request)

    assert len(tasks) == 1
    assert tasks[0].goal == "historical_context"
    assert tasks[0].metadata["research_goals"] == ["historical_context"]


@pytest.mark.asyncio
async def test_extract_emits_source_backed_historical_image_evidence():
    snapshots = _Snapshots()
    payload = _photo_payload(distance=42.5)
    adapter = PastVuHistoricalAdapter(_Fast(_response(payload)), snapshots)
    request = _request(point=Point(latitude=58.009636, longitude=56.239332))
    task = (await adapter.discover(request))[0]
    task.metadata["collection_id"] = "collection-1"
    fetched = _response(payload)

    result = await adapter.extract(task, fetched, request)

    assert not result.errors
    assert not result.blocked
    assert len(result.observations) == 1
    assert len(result.evidence) == 1
    observation = result.observations[0]
    assert observation.source == "pastvu_historical"
    assert observation.source_kind == "historical_image_reference"
    assert observation.entity_type == "image"
    assert observation.entity_id == "pastvu:123456"
    assert observation.url == "https://pastvu.com/p/123456"
    assert observation.data["image_url"] == (
        "https://img.pastvu.com/d/00/11/22/example.jpg"
    )
    assert observation.data["thumbnail_url"] == (
        "https://img.pastvu.com/h/00/11/22/example.jpg"
    )
    assert observation.data["year"] == 1964
    assert observation.data["year2"] == 1965
    assert observation.quality["historical_image"] is True
    assert observation.quality["historical_context_qualified"] is False
    assert "historical_context" not in observation.quality["intent_evidence"]
    assert observation.provenance["historical_image"] is True
    assert observation.provenance["model_output_is_evidence"] is False
    assert result.evidence[0].metadata["source_declared"] is True
    assert result.evidence[0].metadata["supported_intents"] == ["historical_images"]
    assert snapshots.calls

    coverage = IntentCoverageEvaluator().counts([observation], request=request)
    assert coverage["historical_images"] == 1
    assert coverage.get("historical_context", 0) == 0


@pytest.mark.asyncio
async def test_dated_described_nearby_photo_supports_historical_context():
    snapshots = _Snapshots()
    payload = _photo_payload()
    adapter = PastVuHistoricalAdapter(_Fast(_response(payload)), snapshots)
    request = _request(
        point=Point(latitude=58.009636, longitude=56.239332),
        intents=["historical_context", "historical_images"],
    )
    task = (await adapter.discover(request))[0]
    task.metadata["collection_id"] = "collection-context"

    result = await adapter.extract(task, _response(payload), request)

    observation = result.observations[0]
    assert observation.quality["intent_evidence"] == {
        "historical_images": True,
        "historical_context": True,
    }
    context = observation.provenance["historical_context"]
    assert context["version"] == "pastvu-historical-context/1"
    assert context["source_declared_title"] == "Комсомольский проспект"
    assert context["source_declared_year"] == 1964
    assert context["within_research_radius"] is True
    assert 0 <= context["computed_distance_meters"] <= 1000
    assert context["navigation_geocoding_is_evidence"] is False
    assert context["model_output_is_evidence"] is False
    assert result.evidence[0].metadata["supported_intents"] == [
        "historical_images",
        "historical_context",
    ]
    assert result.evidence[0].metadata["historical_context"] == context

    coverage = IntentCoverageEvaluator().counts([observation], request=request)
    assert coverage["historical_images"] == 1
    assert coverage["historical_context"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"year": None},
        {"title": "Без названия"},
        {"geo": [58.1097, 56.2394]},
    ],
)
async def test_incomplete_or_out_of_radius_photo_does_not_fake_historical_context(
    overrides: dict[str, object],
):
    payload = _photo_payload(**overrides)
    adapter = PastVuHistoricalAdapter(_Fast(_response(payload)), _Snapshots())
    request = _request(
        point=Point(latitude=58.009636, longitude=56.239332),
        intents=["historical_context", "historical_images"],
        radius_meters=1000,
    )
    task = (await adapter.discover(request))[0]
    task.metadata["collection_id"] = "collection-negative"

    result = await adapter.extract(task, _response(payload), request)

    observation = result.observations[0]
    assert observation.quality["historical_images"] is True
    assert observation.quality["historical_context_qualified"] is False
    assert "historical_context" not in observation.quality["intent_evidence"]
    assert "historical_context" not in observation.provenance
    coverage = IntentCoverageEvaluator().counts([observation], request=request)
    assert coverage["historical_images"] == 1
    assert coverage.get("historical_context", 0) == 0


@pytest.mark.asyncio
async def test_invalid_api_shape_never_creates_fake_historical_image():
    adapter = PastVuHistoricalAdapter(_Fast(), _Snapshots())
    request = _request(point=Point(latitude=58.009636, longitude=56.239332))
    task = (await adapter.discover(request))[0]

    result = await adapter.extract(task, _response({"result": {}}), request)

    assert result.observations == []
    assert result.evidence == []
    assert [item.code for item in result.errors] == ["PASTVU_API_INVALID_RESPONSE"]
