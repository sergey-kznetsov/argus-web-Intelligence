import pytest
from pydantic import ValidationError

from argus.maps.contracts import (
    MapPlace,
    MapProviderCapabilities,
    MapSearchRequest,
    MapSearchResult,
)
from argus.maps.registry import MapProviderRegistry


class FakeMapProvider:
    provider_id = "fake-map"
    capabilities = MapProviderCapabilities(
        text_search=True,
        category_search=False,
        nearby=True,
    )

    async def search(self, request):
        return MapSearchResult(
            provider=self.provider_id,
            places=[
                MapPlace(
                    provider=self.provider_id,
                    provider_place_id="1",
                    name=request.query or "place",
                    address="Ижевск",
                    point={"latitude": 56.85, "longitude": 53.2},
                    source_url="https://example.com/place/1",
                )
            ],
        )

    async def health(self):
        return {"provider": self.provider_id, "status": "ok"}


def request(**values):
    payload = {
        "territory": {"city": "Ижевск"},
        "query": "детский центр",
        "radius_meters": 1000,
    }
    payload.update(values)
    return MapSearchRequest(**payload)


def test_map_search_requires_query_or_category():
    with pytest.raises(ValidationError):
        MapSearchRequest(territory={"city": "Ижевск"})


def test_map_registry_selects_by_capabilities():
    registry = MapProviderRegistry()
    provider = FakeMapProvider()
    registry.register(provider)
    assert registry.capable_for(request()) == [provider]
    assert registry.capable_for(request(query=None, categories=["education"])) == []


def test_map_registry_rejects_duplicate_provider():
    registry = MapProviderRegistry()
    registry.register(FakeMapProvider())
    with pytest.raises(ValueError):
        registry.register(FakeMapProvider())


@pytest.mark.asyncio
async def test_map_provider_result_preserves_source_provenance():
    result = await FakeMapProvider().search(request())
    assert result.provider == "fake-map"
    assert result.places[0].source_url == "https://example.com/place/1"
    assert result.places[0].point.latitude == 56.85
