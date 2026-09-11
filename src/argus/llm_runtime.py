from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from argus.config import Settings

if TYPE_CHECKING:
    from argus.llm_health import OllamaRuntimeHealth


class LlmConcurrencyGate:
    """One process-wide admission gate for every model-backed ARGUS component."""

    def __init__(self, max_concurrency: int = 1) -> None:
        self.max_concurrency = max(1, int(max_concurrency))
        self._semaphore = asyncio.Semaphore(self.max_concurrency)
        self._in_flight = 0
        self._waiting = 0
        self._peak_in_flight = 0
        self._completed = 0

    @asynccontextmanager
    async def slot(self, component: str) -> AsyncIterator[None]:
        """Serialize model work while keeping acquisition and extraction independent."""

        del component
        self._waiting += 1
        acquired = False
        try:
            await self._semaphore.acquire()
            acquired = True
        finally:
            self._waiting -= 1
        self._in_flight += 1
        self._peak_in_flight = max(self._peak_in_flight, self._in_flight)
        try:
            yield
        finally:
            self._in_flight -= 1
            self._completed += 1
            if acquired:
                self._semaphore.release()

    def snapshot(self) -> dict[str, int]:
        return {
            "max_concurrency": self.max_concurrency,
            "in_flight": self._in_flight,
            "waiting": self._waiting,
            "peak_in_flight": self._peak_in_flight,
            "completed": self._completed,
        }


def ollama_generate_payload(
    settings: Settings,
    prompt: str,
    *,
    json_output: bool = True,
) -> dict[str, object]:
    """Build the single bounded Ollama generation profile used by ARGUS."""

    payload: dict[str, object] = {
        "model": settings.ollama_model,
        "prompt": prompt,
        "stream": False,
        "think": False,
        "keep_alive": f"{settings.ollama_keep_alive_seconds}s",
        "options": {
            "num_thread": settings.ollama_num_thread,
            "num_ctx": settings.ollama_num_ctx,
            "num_predict": settings.ollama_num_predict,
            "temperature": 0,
        },
    }
    if json_output:
        payload["format"] = "json"
    return payload


async def optional_llm_ready(health: OllamaRuntimeHealth | None) -> bool:
    """Return false on any optional-health failure so callers can fail open."""

    if health is None:
        return True
    try:
        return bool((await health.check()).ready)
    except Exception:
        return False
