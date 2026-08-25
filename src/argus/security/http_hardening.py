from __future__ import annotations

import asyncio
import json
import math
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

ASGIReceive = Callable[[], Awaitable[dict[str, Any]]]
ASGISend = Callable[[dict[str, Any]], Awaitable[None]]
ASGIApp = Callable[[dict[str, Any], ASGIReceive, ASGISend], Awaitable[None]]


_SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"cache-control", b"no-store"),
    (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
    (b"cross-origin-resource-policy", b"same-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=()"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
)


class SecurityHeadersMiddleware:
    """Attach conservative security headers to the internal versioned API."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: ASGIReceive,
        send: ASGISend,
    ) -> None:
        if scope.get("type") != "http" or not str(scope.get("path", "")).startswith("/v1/"):
            await self.app(scope, receive, send)
            return

        async def hardened_send(message: dict[str, Any]) -> None:
            if message.get("type") == "http.response.start":
                headers = list(message.get("headers", []))
                present = {name.lower() for name, _ in headers}
                for name, value in _SECURITY_HEADERS:
                    if name not in present:
                        headers.append((name, value))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, hardened_send)


@dataclass(slots=True)
class _Bucket:
    tokens: float
    updated_at: float
    last_seen_at: float


class ClientRateLimitMiddleware:
    """Process-local token bucket keyed only by the direct TCP peer address.

    Forwarded headers are intentionally ignored. Queue admission limits continue to be
    the authoritative per-consumer control; this middleware protects the API process
    itself from a single direct client overwhelming request handling.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        requests_per_minute: float,
        burst: int,
        max_clients: int = 1024,
    ) -> None:
        if requests_per_minute <= 0:
            raise ValueError("requests_per_minute must be positive")
        if burst < 1:
            raise ValueError("burst must be positive")
        if max_clients < 1:
            raise ValueError("max_clients must be positive")
        self.app = app
        self.rate_per_second = float(requests_per_minute) / 60.0
        self.burst = float(burst)
        self.max_clients = int(max_clients)
        self._buckets: dict[str, _Bucket] = {}
        self._lock = asyncio.Lock()

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: ASGIReceive,
        send: ASGISend,
    ) -> None:
        if scope.get("type") != "http" or self._exempt(scope):
            await self.app(scope, receive, send)
            return

        client_key = self._client_key(scope)
        allowed, retry_after = await self._consume(client_key)
        if not allowed:
            await self._send_limited(send, retry_after)
            return
        await self.app(scope, receive, send)

    @staticmethod
    def _exempt(scope: dict[str, Any]) -> bool:
        path = str(scope.get("path", ""))
        method = str(scope.get("method", "GET")).upper()
        return path == "/v1/health" or (method == "HEAD" and path == "/v1/health")

    @staticmethod
    def _client_key(scope: dict[str, Any]) -> str:
        client = scope.get("client")
        if isinstance(client, (list, tuple)) and client:
            return str(client[0])[:128]
        return "unknown"

    async def _consume(self, key: str) -> tuple[bool, int]:
        now = time.monotonic()
        async with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                self._ensure_capacity(now)
                bucket = _Bucket(tokens=self.burst, updated_at=now, last_seen_at=now)
                self._buckets[key] = bucket
            elapsed = max(0.0, now - bucket.updated_at)
            bucket.tokens = min(self.burst, bucket.tokens + elapsed * self.rate_per_second)
            bucket.updated_at = now
            bucket.last_seen_at = now
            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return True, 0
            missing = 1.0 - bucket.tokens
            retry_after = max(1, math.ceil(missing / self.rate_per_second))
            return False, retry_after

    def _ensure_capacity(self, now: float) -> None:
        if len(self._buckets) < self.max_clients:
            return
        stale_before = now - 600.0
        stale = [
            key for key, bucket in self._buckets.items() if bucket.last_seen_at < stale_before
        ]
        for key in stale:
            self._buckets.pop(key, None)
            if len(self._buckets) < self.max_clients:
                return
        if not self._buckets:
            return
        oldest = min(self._buckets.items(), key=lambda item: item[1].last_seen_at)[0]
        self._buckets.pop(oldest, None)

    async def _send_limited(self, send: ASGISend, retry_after: int) -> None:
        body = json.dumps(
            {"detail": {"code": "CLIENT_RATE_LIMITED"}},
            separators=(",", ":"),
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 429,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"retry-after", str(retry_after).encode("ascii")),
                    (b"cache-control", b"no-store"),
                    (b"x-content-type-options", b"nosniff"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
