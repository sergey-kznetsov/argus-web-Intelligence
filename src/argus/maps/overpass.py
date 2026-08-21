from __future__ import annotations

import json
import re
from typing import Any

import httpx

from argus.config import Settings
from argus.contracts.models import Point, StructuredError
from argus.maps.contracts import (
    MapPlace,
    MapProviderCapabilities,
    MapSearchRequest,
    MapSearchResult,
)
from argus.security.redaction import safe_error_message


_CATEGORY_TAGS: dict[str, tuple[str, str]] = {
    "school": ("amenity", "school"),
    "kindergarten": ("amenity", "kindergarten"),
    "college": ("amenity", "college"),
    "university": ("amenity", "university"),
    "hospital": ("amenity", "hospital"),
    "clinic": ("amenity", "clinic"),
    "pharmacy": ("amenity", "pharmacy"),
    "restaurant": ("amenity", "restaurant"),
    "cafe": ("amenity", "cafe"),
    "supermarket": ("shop", "supermarket"),
    "mall": ("shop", "mall"),
    "park": ("leisure", "park"),
}


class OverpassMapProvider:
    provider_id = "openstreetmap_overpass"
    capabilities = MapProviderCapabilities(
        text_search=True,
        category_search=True,
        nearby=True,
        pagination=False,
        place_details=False,
        public_web_only=True,
    )

    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not settings.overpass_url:
            raise ValueError("ARGUS_OVERPASS_URL is required to enable Overpass map search")
        self.settings = settings
        self.endpoint = settings.overpass_url
        self.transport = transport

    async def search(self, request: MapSearchRequest) -> MapSearchResult:
        if request.territory.point is None:
            return self._error(
                "MAP_LOCATION_REQUIRED",
                "Overpass nearby search requires territory.point coordinates",
                retryable=False,
            )
        radius = request.radius_meters or request.territory.radius_meters or 1_000
        unsupported = [item for item in request.categories if item not in _CATEGORY_TAGS]
        if unsupported:
            return self._error(
                "MAP_CATEGORY_UNSUPPORTED",
                "Unsupported map categories: " + ", ".join(sorted(unsupported)),
                retryable=False,
            )

        query = self._build_query(request, radius)
        try:
            data, status_code = await self._request_json(query)
        except Exception as exc:
            return self._error(
                "MAP_PROVIDER_ERROR",
                safe_error_message(exc, max_length=300),
                retryable=True,
            )
        if status_code in {403, 429}:
            return MapSearchResult(
                provider=self.provider_id,
                blocked=True,
                errors=[
                    StructuredError(
                        code="MAP_PROVIDER_BLOCKED",
                        message=f"Overpass returned HTTP {status_code}",
                        retryable=True,
                        source_id=f"map:{self.provider_id}",
                    )
                ],
            )

        elements = data.get("elements", [])
        if not isinstance(elements, list):
            return self._error(
                "MAP_PROVIDER_INVALID_RESPONSE",
                "Overpass response does not contain an elements array",
                retryable=True,
            )

        places: list[MapPlace] = []
        for element in elements:
            if not isinstance(element, dict):
                continue
            place = self._place_from_element(element)
            if place is not None:
                places.append(place)
            if len(places) >= request.limit:
                break
        return MapSearchResult(provider=self.provider_id, places=places)

    async def health(self) -> dict[str, object]:
        return {"provider": self.provider_id, "status": "configured"}

    def _build_query(self, request: MapSearchRequest, radius: int) -> str:
        point = request.territory.point
        if point is None:
            raise ValueError("territory.point is required")
        filters: list[tuple[str, str] | None]
        if request.categories:
            filters = [_CATEGORY_TAGS[item] for item in request.categories]
        else:
            filters = [None]

        name_filter = ""
        if request.query and request.query.strip():
            pattern = self._ql_string(re.escape(request.query.strip()))
            name_filter = f'["name"~"{pattern}",i]'

        selectors: list[str] = []
        for tag in filters:
            tag_filter = ""
            if tag is not None:
                key, value = tag
                tag_filter = f'["{self._ql_string(key)}"="{self._ql_string(value)}"]'
            for element_type in ("node", "way", "relation"):
                selectors.append(
                    f"{element_type}(around:{radius},{point.latitude},{point.longitude})"
                    f"{tag_filter}{name_filter};"
                )
        body = "".join(selectors)
        return f"[out:json][timeout:25];({body});out center tags;"

    async def _request_json(self, query: str) -> tuple[dict[str, Any], int]:
        timeout = httpx.Timeout(self.settings.overpass_timeout_seconds)
        headers = {
            "Accept": "application/json",
            "User-Agent": "ARGUS-Web-Intelligence/0.1",
        }
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
            transport=self.transport,
            headers=headers,
        ) as client:
            async with client.stream("POST", self.endpoint or "", data={"data": query}) as response:
                status_code = response.status_code
                if status_code not in {403, 429}:
                    response.raise_for_status()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > self.settings.max_response_bytes:
                        raise ValueError("Overpass response exceeds configured limit")
        if status_code in {403, 429}:
            return {}, status_code
        parsed = json.loads(body.decode("utf-8", errors="strict"))
        if not isinstance(parsed, dict):
            raise ValueError("Overpass returned a non-object JSON response")
        return parsed, status_code

    def _place_from_element(self, element: dict[str, Any]) -> MapPlace | None:
        element_type = str(element.get("type") or "").strip()
        element_id = element.get("id")
        tags_raw = element.get("tags") or {}
        tags = tags_raw if isinstance(tags_raw, dict) else {}
        name = str(tags.get("name") or tags.get("brand") or tags.get("operator") or "").strip()
        if element_type not in {"node", "way", "relation"} or element_id is None or not name:
            return None

        lat = element.get("lat")
        lon = element.get("lon")
        center = element.get("center")
        if (lat is None or lon is None) and isinstance(center, dict):
            lat = center.get("lat")
            lon = center.get("lon")
        point = None
        try:
            if lat is not None and lon is not None:
                point = Point(latitude=float(lat), longitude=float(lon))
        except (TypeError, ValueError):
            point = None

        source_url = f"https://www.openstreetmap.org/{element_type}/{element_id}"
        categories = self._categories_from_tags(tags)
        return MapPlace(
            provider=self.provider_id,
            provider_place_id=f"{element_type}/{element_id}",
            name=name,
            address=self._address_from_tags(tags),
            point=point,
            categories=categories,
            source_url=source_url,
            attributes={"osm_tags": tags},
            provenance={
                "retrieval": "overpass",
                "attribution": "© OpenStreetMap contributors",
                "data_license": "ODbL",
            },
        )

    def _error(self, code: str, message: str, *, retryable: bool) -> MapSearchResult:
        return MapSearchResult(
            provider=self.provider_id,
            errors=[
                StructuredError(
                    code=code,
                    message=message,
                    retryable=retryable,
                    source_id=f"map:{self.provider_id}",
                )
            ],
        )

    @staticmethod
    def _categories_from_tags(tags: dict[str, Any]) -> list[str]:
        values: list[str] = []
        for key in ("amenity", "shop", "leisure", "tourism", "office", "healthcare"):
            value = str(tags.get(key) or "").strip()
            if value:
                values.append(f"{key}:{value}")
        return values

    @staticmethod
    def _address_from_tags(tags: dict[str, Any]) -> str | None:
        full = str(tags.get("addr:full") or "").strip()
        if full:
            return full
        city = str(tags.get("addr:city") or "").strip()
        street = str(tags.get("addr:street") or "").strip()
        number = str(tags.get("addr:housenumber") or "").strip()
        street_part = " ".join(item for item in (street, number) if item)
        parts = [item for item in (city, street_part) if item]
        return ", ".join(parts) or None

    @staticmethod
    def _ql_string(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')
