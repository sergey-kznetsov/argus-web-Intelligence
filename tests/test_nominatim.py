import json

import httpx
import pytest

from argus.config import Settings
from argus.geocoding.nominatim import NominatimGeocoder


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

    settings = Settings(
        db_path=tmp_path / "db.sqlite",
        token_file=tmp_path / "token",
        nominatim_url="http://127.0.0.1:8080",
    )
    geocoder = NominatimGeocoder(settings, transport=httpx.MockTransport(handler))
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
async def test_nominatim_rate_limit_is_blocked(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, content=b"rate limited", request=request)

    settings = Settings(
        db_path=tmp_path / "db.sqlite",
        token_file=tmp_path / "token",
        nominatim_url="http://127.0.0.1:8080",
    )
    geocoder = NominatimGeocoder(settings, transport=httpx.MockTransport(handler))
    result = await geocoder.search("Ижевск")

    assert result.blocked is True
    assert result.candidates == []
    assert result.errors[0].code == "GEOCODING_PROVIDER_BLOCKED"


@pytest.mark.asyncio
async def test_nominatim_empty_result_is_explicit(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"[]", request=request)

    settings = Settings(
        db_path=tmp_path / "db.sqlite",
        token_file=tmp_path / "token",
        nominatim_url="http://127.0.0.1:8080",
    )
    geocoder = NominatimGeocoder(settings, transport=httpx.MockTransport(handler))
    result = await geocoder.search("несуществующий адрес")

    assert result.blocked is False
    assert result.candidates == []
    assert result.errors[0].code == "GEOCODING_NO_RESULTS"
