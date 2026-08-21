from __future__ import annotations

from typing import Protocol

from argus.maps.contracts import (
    MapProviderCapabilities,
    MapSearchRequest,
    MapSearchResult,
)


class MapProvider(Protocol):
    provider_id: str
    capabilities: MapProviderCapabilities

    async def search(self, request: MapSearchRequest) -> MapSearchResult: ...

    async def health(self) -> dict[str, object]: ...
