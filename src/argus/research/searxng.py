from __future__ import annotations

import json
from typing import Any
from urllib.parse import urljoin

import httpx

from argus.config import Settings
from argus.contracts.models import CollectionRequest
from argus.research.discovery import DiscoveryHit


class SearxngDiscoveryProvider:
    name = "searxng"

    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not settings.searxng_url:
            raise ValueError("ARGUS_SEARXNG_URL is required to enable SearXNG discovery")
        self.settings = settings
        self.endpoint = urljoin(settings.searxng_url.rstrip("/") + "/", "search")
        self.transport = transport

    async def discover(
        self,
        queries: list[str],
        request: CollectionRequest,
    ) -> list[DiscoveryHit]:
        hits: list[DiscoveryHit] = []
        seen: set[str] = set()
        timeout = httpx.Timeout(self.settings.searxng_timeout_seconds)
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
            transport=self.transport,
        ) as client:
            for query in queries:
                payload: dict[str, str] = {
                    "q": query,
                    "format": "json",
                    "safesearch": "1",
                }
                if request.constraints.language:
                    payload["language"] = request.constraints.language
                data = await self._request_json(client, payload)
                results = data.get("results", [])
                if not isinstance(results, list):
                    continue
                for position, item in enumerate(
                    results[: self.settings.searxng_max_results_per_query],
                    start=1,
                ):
                    if not isinstance(item, dict):
                        continue
                    url = str(item.get("url") or "").strip()
                    if not url.startswith(("http://", "https://")) or url in seen:
                        continue
                    seen.add(url)
                    engines_raw = item.get("engines") or []
                    engines = (
                        [str(engine) for engine in engines_raw]
                        if isinstance(engines_raw, list)
                        else []
                    )
                    if not engines and item.get("engine"):
                        engines = [str(item["engine"])]
                    title = str(item.get("title") or "").strip() or None
                    hits.append(
                        DiscoveryHit(
                            url=url,
                            provider=self.name,
                            title=title,
                            engines=engines,
                            rank=position,
                        )
                    )
        return hits

    async def _request_json(
        self,
        client: httpx.AsyncClient,
        payload: dict[str, str],
    ) -> dict[str, Any]:
        async with client.stream("POST", self.endpoint, data=payload) as response:
            response.raise_for_status()
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > self.settings.max_response_bytes:
                    raise ValueError("SearXNG response exceeds configured limit")
        parsed = json.loads(body.decode("utf-8", errors="strict"))
        if not isinstance(parsed, dict):
            raise ValueError("SearXNG returned a non-object JSON response")
        return parsed

    async def health(self) -> dict[str, object]:
        try:
            timeout = httpx.Timeout(self.settings.searxng_timeout_seconds)
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=False,
                trust_env=False,
                transport=self.transport,
            ) as client:
                response = await client.get(self.settings.searxng_url or "")
            return {
                "provider": self.name,
                "status": "ok" if response.status_code < 500 else "degraded",
                "status_code": response.status_code,
            }
        except httpx.HTTPError:
            return {"provider": self.name, "status": "unavailable"}
