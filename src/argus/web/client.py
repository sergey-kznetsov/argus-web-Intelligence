from __future__ import annotations

from typing import Any

import httpx

from argus.web.config import WebSettings


class ArgusApiClient:
    """Small fixed-surface client for the local ARGUS API used by the web gateway."""

    def __init__(self, settings: WebSettings) -> None:
        self.settings = settings
        self._client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.settings.api_url,
                timeout=self.settings.request_timeout_seconds,
                trust_env=False,
            )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def request_json(
        self,
        method: str,
        path: str,
        *,
        json_body: object | None = None,
        params: dict[str, object] | None = None,
    ) -> tuple[int, object]:
        if self._client is None:
            raise RuntimeError("ARGUS API client is not started")
        token = self._read_api_token()
        response = await self._client.request(
            method,
            path,
            headers={"Authorization": f"Bearer {token}"},
            json=json_body,
            params=params,
        )
        try:
            payload: Any = response.json()
        except ValueError:
            payload = {
                "detail": "ARGUS API returned a non-JSON response",
                "upstream_status": response.status_code,
            }
        return response.status_code, payload

    def _read_api_token(self) -> str:
        try:
            token = self.settings.api_token_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise RuntimeError("ARGUS API token file is unavailable") from exc
        if not token:
            raise RuntimeError("ARGUS API token file is empty")
        return token
