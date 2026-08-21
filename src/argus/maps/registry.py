from __future__ import annotations

from argus.maps.base import MapProvider
from argus.maps.contracts import MapSearchRequest


class MapProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, MapProvider] = {}

    def register(self, provider: MapProvider) -> None:
        provider_id = provider.provider_id.strip()
        if not provider_id:
            raise ValueError("map provider_id is required")
        if provider_id in self._providers:
            raise ValueError(f"map provider already registered: {provider_id}")
        self._providers[provider_id] = provider

    def get(self, provider_id: str) -> MapProvider:
        try:
            return self._providers[provider_id]
        except KeyError as exc:
            raise KeyError(f"unknown map provider: {provider_id}") from exc

    def all(self) -> list[MapProvider]:
        return list(self._providers.values())

    def capable_for(self, request: MapSearchRequest) -> list[MapProvider]:
        providers: list[MapProvider] = []
        for provider in self._providers.values():
            capabilities = provider.capabilities
            if request.query and not capabilities.text_search:
                continue
            if request.categories and not capabilities.category_search:
                continue
            if request.radius_meters and not capabilities.nearby:
                continue
            providers.append(provider)
        return providers
