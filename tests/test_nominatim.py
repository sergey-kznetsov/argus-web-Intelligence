import json

import httpx
import pytest

from argus.config import Settings
from argus.geocoding.nominatim import NominatimGeocoder


def settings(tmp_path, **values):
    defaults = {
        "db_path": tmp_path / "db.sqlite",
        "token_file": tmp_path / "token",
        "nominatim_url": "http://127.0.0.1:8080",
        "nominatim_min_interval_seconds": 0,
        "direct_provider_retry_base_seconds": 0,
        "direct_provider_retry_max_seconds": 0,
    }
    defaults.update(values)
    return Settings(**defaults)


@pytest.mark.asyncio
async def test_nominatim_geocoder_normalizes_first_candidates(tmp_path):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = [
            {
                "place_id": 123,
                "osm_type": "way",
                "osm_id": 456,
                "lat": "56.8500",
                "lon": "53.2000",
                "display_name": "Пушкинская улица, Ижевск, Россия",
                "importance": 0.7,
                "category": "highway",
                "type": "residential",
                "address": {"city": "Ижевск", "road": "Пушкинская улица"},
            }
        ]
        return httpx.Response(200, content=json.dumps(body).encode(), request=request)

    geocoder = NominatimGeocoder(settings(tmp_path), transport=httpx.MockTransport(handler))
    result = await geocoder.search("Ижевск, Пушкинская 1", limit=1, language="ru")

    assert result.errors == []
    assert result.blocked is False
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.point.latitude == 56.85
    assert candidate.point.longitude == 53.2
    assert candidate.source_url == "https://www.openstreetmap.org/way/456"
    assert candidate.provenance["data_license"] == "ODbL"
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert requests[0].url.path == "/search"
    assert requests[0].url.params["format"] == "jsonv2"
    assert requests[0].url.params["q"] == "Ижевск, Пушкинская 1"
    assert requests[0].url.params["accept-language"] == "ru"
    assert "ARGUS-Web-Intelligence" in requests[0].headers["user-agent"]


@pytest.mark.asyncio
async def test_nominatim_fallback_source_is_public_osm_search(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        body = [
            {
                "place_id": 123,
                "lat": "56.8500",
                "lon": "53.2000",
                "display_name": "Пушкинская улица, Ижевск, Россия",
            }
        ]
        return httpx.Response(200, content=json.dumps(body).encode(), request=request)

    geocoder = NominatimGeocoder(settings(tmp_path), transport=httpx.MockTransport(handler))
    result = await geocoder.search("Ижевск, Пушкинская")
    assert result.candidates[0].source_url.startswith("https://www.openstreetmap.org/search?query=")
    assert "127.0.0.1" not in result.candidates[0].source_url


@pytest.mark.asyncio
async def test_nominatim_rate_limit_is_blocked_after_retry_budget(tmp_path):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, content=b"rate limited", request=request)

    geocoder = NominatimGeocoder(
        settings(tmp_path, direct_provider_max_retries=1),
        transport=httpx.MockTransport(handler),
    )
    result = await geocoder.search("Ижевск")

    assert calls == 2
    assert result.blocked is True
    assert result.candidates == []
    assert result.errors[0].code == "GEOCODING_PROVIDER_BLOCKED"


@pytest.mark.asyncio
async def test_nominatim_does_not_retry_before_long_retry_after(tmp_path):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            429,
            headers={"Retry-After": "120"},
            content=b"rate limited",
            request=request,
        )

    geocoder = NominatimGeocoder(
        settings(
            tmp_path,
            direct_provider_max_retries=2,
            direct_provider_retry_max_seconds=30,
        ),
        transport=httpx.MockTransport(handler),
    )
    result = await geocoder.search("Ижевск")

    assert calls == 1
    assert result.blocked is True
    assert result.errors[0].code == "GEOCODING_PROVIDER_BLOCKED"


@pytest.mark.asyncio
async def test_nominatim_retries_503_then_succeeds(tmp_path):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, content=b"temporary", request=request)
        return httpx.Response(
            200,
            content=json.dumps(
                [
                    {
                        "place_id": 1,
                        "osm_type": "node",
                        "osm_id": 2,
                        "lat": "56.85",
                        "lon": "53.2",
                        "display_name": "Ижевск",
                    }
                ]
            ).encode(),
            request=request,
        )

    geocoder = NominatimGeocoder(
        settings(tmp_path, direct_provider_max_retries=1),
        transport=httpx.MockTransport(handler),
    )
    result = await geocoder.search("Ижевск")
    assert calls == 2
    assert result.errors == []
    assert len(result.candidates) == 1


@pytest.mark.asyncio
async def test_nominatim_empty_result_is_explicit(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"[]", request=request)

    geocoder = NominatimGeocoder(settings(tmp_path), transport=httpx.MockTransport(handler))
    result = await geocoder.search("несуществующий адрес")

    assert result.blocked is False
    assert result.candidates == []
    assert result.errors[0].code == "GEOCODING_NO_RESULTS"
