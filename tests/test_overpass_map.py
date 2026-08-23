from urllib.parse import parse_qs

import httpx
import pytest

from argus.config import Settings
from argus.maps.contracts import MapSearchRequest
from argus.maps.overpass import OverpassMapProvider


def map_request(**values):
    payload = {
        "territory": {
            "city": "Ижевск",
            "point": {"latitude": 56.85, "longitude": 53.2},
        },
        "categories": ["school"],
        "radius_meters": 1000,
        "limit": 10,
    }
    payload.update(values)
    return MapSearchRequest(**payload)


def settings(**values):
    defaults = {
        "overpass_url": "https://overpass.example/api/interpreter",
        "overpass_min_interval_seconds": 0,
        "direct_provider_retry_base_seconds": 0,
        "direct_provider_retry_max_seconds": 0,
    }
    defaults.update(values)
    return Settings(**defaults)


@pytest.mark.asyncio
async def test_overpass_normalizes_places_and_provenance():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        form = parse_qs(request.content.decode())
        query = form["data"][0]
        assert '["amenity"="school"]' in query
        assert "around:1000,56.85,53.2" in query
        return httpx.Response(
            200,
            json={
                "elements": [
                    {
                        "type": "node",
                        "id": 42,
                        "lat": 56.851,
                        "lon": 53.201,
                        "tags": {
                            "name": "Школа 1",
                            "amenity": "school",
                            "addr:city": "Ижевск",
                            "addr:street": "Пушкинская",
                            "addr:housenumber": "1",
                        },
                    }
                ]
            },
        )

    provider = OverpassMapProvider(settings(), transport=httpx.MockTransport(handler))
    result = await provider.search(map_request())
    assert not result.errors
    assert result.places[0].name == "Школа 1"
    assert result.places[0].address == "Ижевск, Пушкинская 1"
    assert result.places[0].source_url == "https://www.openstreetmap.org/node/42"
    assert result.places[0].categories == ["amenity:school"]
    assert result.places[0].provenance["data_license"] == "ODbL"


@pytest.mark.asyncio
async def test_overpass_rejects_unknown_category_before_request():
    called = False

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"elements": []})

    provider = OverpassMapProvider(settings(), transport=httpx.MockTransport(handler))
    result = await provider.search(map_request(categories=["unknown-category"]))
    assert result.errors[0].code == "MAP_CATEGORY_UNSUPPORTED"
    assert called is False


@pytest.mark.asyncio
async def test_overpass_rate_limit_is_blocked_after_retry_budget():
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        del request
        calls += 1
        return httpx.Response(429, text="rate limited")

    provider = OverpassMapProvider(
        settings(direct_provider_max_retries=1),
        transport=httpx.MockTransport(handler),
    )
    result = await provider.search(map_request())
    assert calls == 2
    assert result.blocked is True
    assert result.errors[0].code == "MAP_PROVIDER_BLOCKED"


@pytest.mark.asyncio
async def test_overpass_does_not_retry_before_long_retry_after():
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        del request
        calls += 1
        return httpx.Response(
            429,
            headers={"Retry-After": "120"},
            text="rate limited",
        )

    provider = OverpassMapProvider(
        settings(
            direct_provider_max_retries=2,
            direct_provider_retry_max_seconds=30,
        ),
        transport=httpx.MockTransport(handler),
    )
    result = await provider.search(map_request())
    assert calls == 1
    assert result.blocked is True
    assert result.errors[0].code == "MAP_PROVIDER_BLOCKED"


@pytest.mark.asyncio
async def test_overpass_retries_503_then_succeeds():
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        del request
        calls += 1
        if calls == 1:
            return httpx.Response(503, text="temporary")
        return httpx.Response(200, json={"elements": []})

    provider = OverpassMapProvider(
        settings(direct_provider_max_retries=1),
        transport=httpx.MockTransport(handler),
    )
    result = await provider.search(map_request())
    assert calls == 2
    assert result.errors == []
    assert result.places == []


def test_overpass_text_query_is_regex_escaped():
    provider = OverpassMapProvider(settings())
    query = provider._build_query(map_request(query='Школа [1] "центр"'), 1000)
    assert "\\[1\\]" in query
    assert '\\"центр\\"' in query
