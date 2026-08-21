from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

from argus.config import Settings
from argus.contracts.models import StructuredError
from argus.network.rate_gate import AsyncRateGate
from argus.network.retry import RETRYABLE_PROVIDER_STATUSES, retry_delay_seconds
from argus.security.redaction import safe_error_message


@dataclass(slots=True)
class WaybackCapture:
    timestamp: str
    original_url: str
    capture_url: str
    mimetype: str | None = None
    status_code: int | None = None
    digest: str | None = None
    length: int | None = None
    captured_at: datetime | None = None


@dataclass(slots=True)
class WaybackCaptureResult:
    captures: list[WaybackCapture] = field(default_factory=list)
    blocked: bool = False
    errors: list[StructuredError] = field(default_factory=list)


class WaybackCDXProvider:
    """Exact-URL capture discovery through a configured Wayback CDX endpoint."""

    provider_id = "wayback_cdx"
    _FIELDS = ("timestamp", "original", "mimetype", "statuscode", "digest", "length")

    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not settings.wayback_cdx_url:
            raise ValueError("ARGUS_WAYBACK_CDX_URL is required to enable Wayback CDX")
        self.settings = settings
        self.endpoint = settings.wayback_cdx_url
        self.capture_base = settings.wayback_capture_base_url
        self.transport = transport
        self.rate_gate = AsyncRateGate(settings.wayback_min_interval_seconds)

    async def captures(self, url: str, *, limit: int | None = None) -> WaybackCaptureResult:
        target = url.strip()
        parsed_target = urlsplit(target)
        if parsed_target.scheme not in {"http", "https"} or not parsed_target.hostname:
            return self._error(
                "ARCHIVE_URL_INVALID",
                "Wayback capture discovery requires an absolute public HTTP(S) URL",
                retryable=False,
            )

        capture_limit = max(1, min(limit or self.settings.wayback_max_captures, 20))
        params = {
            "url": target,
            "matchType": "exact",
            "output": "json",
            "fl": ",".join(self._FIELDS),
            "filter": "statuscode:200",
            "collapse": "digest",
            "limit": str(capture_limit),
        }
        try:
            payload, status_code = await self._request_json(params)
        except Exception as exc:
            return self._error(
                "ARCHIVE_PROVIDER_ERROR",
                safe_error_message(exc, max_length=300),
                retryable=True,
            )

        if status_code in {403, 429}:
            return WaybackCaptureResult(
                blocked=True,
                errors=[
                    StructuredError(
                        code="ARCHIVE_PROVIDER_BLOCKED",
                        message=f"Wayback CDX returned HTTP {status_code}",
                        retryable=True,
                        source_id=f"archive:{self.provider_id}",
                    )
                ],
            )

        return WaybackCaptureResult(captures=self._parse_payload(payload, capture_limit))

    async def health(self) -> dict[str, object]:
        return {
            "provider": self.provider_id,
            "status": "configured",
            "mode": "exact_url",
            "max_captures": self.settings.wayback_max_captures,
            "min_interval_seconds": self.settings.wayback_min_interval_seconds,
        }

    async def _request_json(self, params: dict[str, str]) -> tuple[list[Any], int]:
        timeout = httpx.Timeout(self.settings.wayback_timeout_seconds)
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
            for attempt in range(self.settings.direct_provider_max_retries + 1):
                await self.rate_gate.wait()
                retry_delay: float | None = None
                async with client.stream("GET", self.endpoint, params=params) as response:
                    status_code = response.status_code
                    if (
                        status_code in RETRYABLE_PROVIDER_STATUSES
                        and attempt < self.settings.direct_provider_max_retries
                    ):
                        retry_delay = retry_delay_seconds(
                            attempt=attempt,
                            headers=response.headers,
                            base_delay_seconds=self.settings.direct_provider_retry_base_seconds,
                            max_delay_seconds=self.settings.direct_provider_retry_max_seconds,
                        )
                    else:
                        if status_code not in {403, 429}:
                            response.raise_for_status()
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            body.extend(chunk)
                            if len(body) > self.settings.max_response_bytes:
                                raise ValueError("Wayback CDX response exceeds configured limit")
                        if status_code in {403, 429}:
                            return [], status_code
                        parsed = json.loads(body.decode("utf-8", errors="strict"))
                        if not isinstance(parsed, list):
                            raise ValueError("Wayback CDX returned a non-array JSON response")
                        return parsed, status_code
                if retry_delay is not None:
                    await asyncio.sleep(retry_delay)
        raise RuntimeError("Wayback CDX retry loop exhausted unexpectedly")

    def _parse_payload(self, payload: list[Any], limit: int) -> list[WaybackCapture]:
        if len(payload) < 2 or not isinstance(payload[0], list):
            return []
        header = [str(item) for item in payload[0]]
        indexes = {name: header.index(name) for name in self._FIELDS if name in header}
        if "timestamp" not in indexes or "original" not in indexes:
            return []

        captures: list[WaybackCapture] = []
        seen: set[tuple[str, str]] = set()
        for raw in payload[1:]:
            if not isinstance(raw, list):
                continue
            timestamp = self._cell(raw, indexes.get("timestamp"))
            original = self._cell(raw, indexes.get("original"))
            if not timestamp or not original:
                continue
            parsed_original = urlsplit(original)
            if parsed_original.scheme not in {"http", "https"} or not parsed_original.hostname:
                continue
            identity = (timestamp, original)
            if identity in seen:
                continue
            seen.add(identity)
            captures.append(
                WaybackCapture(
                    timestamp=timestamp,
                    original_url=original,
                    capture_url=f"{self.capture_base}/{timestamp}id_/{original}",
                    mimetype=self._cell(raw, indexes.get("mimetype")) or None,
                    status_code=self._int_cell(raw, indexes.get("statuscode")),
                    digest=self._cell(raw, indexes.get("digest")) or None,
                    length=self._int_cell(raw, indexes.get("length")),
                    captured_at=self._timestamp(timestamp),
                )
            )
            if len(captures) >= limit:
                break
        return captures

    def _error(self, code: str, message: str, *, retryable: bool) -> WaybackCaptureResult:
        return WaybackCaptureResult(
            errors=[
                StructuredError(
                    code=code,
                    message=message,
                    retryable=retryable,
                    source_id=f"archive:{self.provider_id}",
                )
            ]
        )

    @staticmethod
    def _cell(row: list[Any], index: int | None) -> str:
        if index is None or index >= len(row):
            return ""
        return str(row[index] or "").strip()

    @classmethod
    def _int_cell(cls, row: list[Any], index: int | None) -> int | None:
        value = cls._cell(row, index)
        if not value or value == "-":
            return None
        try:
            return int(value)
        except ValueError:
            return None

    @staticmethod
    def _timestamp(value: str) -> datetime | None:
        if len(value) != 14 or not value.isdigit():
            return None
        try:
            return datetime.strptime(value, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
        except ValueError:
            return None
