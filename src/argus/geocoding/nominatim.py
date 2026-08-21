from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote_plus, urljoin

import httpx

from argus.config import Settings
from argus.contracts.models import Point, StructuredError
from argus.geocoding.contracts import GeocodeCandidate, GeocodeResult
from argus.network.rate_gate import AsyncRateGate
from argus.security.redaction import safe_error_message


class NominatimGeocoder:
    provider_id = "nominatim"

    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not settings.nominatim_url:
            raise ValueError("ARGUS_NOMINATIM_URL is required to enable Nominatim geocoding")
        self.settings = settings
        self.endpoint = urljoin(settings.nominatim_url.rstrip("/") + "/", "search")
        self.transport = transport
        self.rate_gate = AsyncRateGate(settings.nominatim_min_interval_seconds)

    async def search(
        self,
        query: str,
        *,
        limit: int = 3,
        language: str | None = None,
    ) -> GeocodeResult:
        value = query.strip()
        if not value:
            return self._error(
                "GEOCODING_QUERY_REQUIRED",
                "Geocoding requires a non-empty address or place query",
                retryable=False,
            )
        params = {
            "q": value,
            "format": "jsonv2",
            "addressdetails": "1",
            "limit": str(max(1, min(limit, self.settings.nominatim_max_results))),
        }
        if language:
            params["accept-language"] = language

        try:
            payload, status_code = await self._request_json(params)
        except Exception as exc:
            return self._error(
                "GEOCODING_PROVIDER_ERROR",
                safe_error_message(exc, max_length=300),
                retryable=True,
            )
        if status_code in {403, 429}:
            return GeocodeResult(
                provider=self.provider_id,
                blocked=True,
                errors=[
                    StructuredError(
                        code="GEOCODING_PROVIDER_BLOCKED",
                        message=f"Nominatim returned HTTP {status_code}",
                        retryable=True,
                        source_id=f"geocoding:{self.provider_id}",
                    )
                ],
            )

        candidates: list[GeocodeCandidate] = []
        for item in payload:
            candidate = self._candidate(item)
            if candidate is not None:
                candidates.append(candidate)
        if not candidates:
            return GeocodeResult(
                provider=self.provider_id,
                errors=[
                    StructuredError(
                        code="GEOCODING_NO_RESULTS",
                        message="Nominatim returned no valid coordinate candidates",
                        retryable=False,
                        source_id=f"geocoding:{self.provider_id}",
                    )
                ],
            )
        return GeocodeResult(provider=self.provider_id, candidates=candidates)

    async def health(self) -> dict[str, object]:
        return {
            "provider": self.provider_id,
            "status": "configured",
            "min_interval_seconds": self.settings.nominatim_min_interval_seconds,
        }

    async def _request_json(self, params: dict[str, str]) -> tuple[list[Any], int]:
        await self.rate_gate.wait()
        timeout = httpx.Timeout(self.settings.nominatim_timeout_seconds)
        headers = {
            "Accept": "application/json",
            "User-Agent": (
                "ARGUS-Web-Intelligence/0.1 "
                "(+https://github.com/sergey-kznetsov/argus-web-Intelligence)"
            ),
        }
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
            transport=self.transport,
            headers=headers,
        ) as client:
            async with client.stream("GET", self.endpoint, params=params) as response:
                status_code = response.status_code
                if status_code not in {403, 429}:
                    response.raise_for_status()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > self.settings.max_response_bytes:
                        raise ValueError("Nominatim response exceeds configured limit")
        if status_code in {403, 429}:
            return [], status_code
        parsed = json.loads(body.decode("utf-8", errors="strict"))
        if not isinstance(parsed, list):
            raise ValueError("Nominatim returned a non-array JSON response")
        return parsed, status_code

    def _candidate(self, item: Any) -> GeocodeCandidate | None:
        if not isinstance(item, dict):
            return None
        display_name = str(item.get("display_name") or "").strip()
        lat = item.get("lat")
        lon = item.get("lon")
        if not display_name or lat is None or lon is None:
            return None
        try:
            point = Point(latitude=float(lat), longitude=float(lon))
        except (TypeError, ValueError):
            return None

        osm_type = str(item.get("osm_type") or "").lower().strip()
        osm_id = item.get("osm_id")
        source_url = (
            "https://www.openstreetmap.org/search?query=" + quote_plus(display_name)
        )
        type_segment = {
            "n": "node",
            "node": "node",
            "w": "way",
            "way": "way",
            "r": "relation",
            "relation": "relation",
        }.get(osm_type)
        if type_segment and osm_id is not None:
            source_url = f"https://www.openstreetmap.org/{type_segment}/{osm_id}"

        importance_raw = item.get("importance")
        importance = None
        try:
            if importance_raw is not None:
                importance = float(importance_raw)
        except (TypeError, ValueError):
            importance = None

        provider_place_id = str(item.get("place_id") or "").strip() or None
        address = item.get("address")
        attributes = {
            "category": item.get("category"),
            "type": item.get("type"),
            "address": address if isinstance(address, dict) else {},
            "osm_type": osm_type or None,
            "osm_id": osm_id,
        }
        return GeocodeCandidate(
            provider=self.provider_id,
            provider_place_id=provider_place_id,
            display_name=display_name,
            point=point,
            source_url=source_url,
            importance=importance,
            attributes=attributes,
            provenance={
                "retrieval": "nominatim",
                "attribution": "© OpenStreetMap contributors",
                "data_license": "ODbL",
            },
        )

    def _error(self, code: str, message: str, *, retryable: bool) -> GeocodeResult:
        return GeocodeResult(
            provider=self.provider_id,
            errors=[
                StructuredError(
                    code=code,
                    message=message,
                    retryable=retryable,
                    source_id=f"geocoding:{self.provider_id}",
                )
            ],
        )
