from __future__ import annotations

import json
import time
from dataclasses import dataclass

import httpx

from argus.config import Settings


@dataclass(frozen=True, slots=True)
class LlmHealth:
    status: str
    ready: bool
    backend: str
    model: str
    reason_code: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "ready": self.ready,
            "backend": self.backend,
            "model": self.model,
            "reason_code": self.reason_code,
        }


class OllamaRuntimeHealth:
    """Check the local Ollama server and configured model without generating text."""

    max_response_bytes = 1024 * 1024

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._cached: LlmHealth | None = None
        self._cached_at = 0.0

    async def check(self, *, force: bool = False) -> LlmHealth:
        now = time.monotonic()
        if (
            not force
            and self._cached is not None
            and now - self._cached_at <= self.settings.llm_health_cache_seconds
        ):
            return self._cached
        value = await self._check_uncached()
        self._cached = value
        self._cached_at = now
        return value

    async def _check_uncached(self) -> LlmHealth:
        endpoint = f"{self.settings.ollama_url.rstrip('/')}/api/tags"
        try:
            timeout = httpx.Timeout(self.settings.llm_health_timeout_seconds)
            async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
                async with client.stream("GET", endpoint) as response:
                    response.raise_for_status()
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > self.max_response_bytes:
                            return self._result(
                                "degraded",
                                False,
                                "OLLAMA_HEALTH_RESPONSE_TOO_LARGE",
                            )
            payload = json.loads(body.decode("utf-8", errors="strict"))
        except (httpx.HTTPError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            return self._result("unavailable", False, "OLLAMA_UNAVAILABLE")

        models = payload.get("models") if isinstance(payload, dict) else None
        if not isinstance(models, list):
            return self._result("degraded", False, "OLLAMA_INVALID_TAGS_RESPONSE")
        available: set[str] = set()
        for item in models:
            if not isinstance(item, dict):
                continue
            for key in ("name", "model"):
                value = str(item.get(key) or "").strip()
                if value:
                    available.add(value)
        if self.settings.ollama_model not in available:
            return self._result("degraded", False, "OLLAMA_MODEL_MISSING")
        return self._result("ok", True, None)

    def _result(self, status: str, ready: bool, reason_code: str | None) -> LlmHealth:
        return LlmHealth(
            status=status,
            ready=ready,
            backend="ollama",
            model=self.settings.ollama_model,
            reason_code=reason_code,
        )
