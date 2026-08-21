from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime

from argus.contracts.models import CollectionRequest, utcnow
from argus.sources.base import SourceAdapter, SourceResult, SourceTask


@dataclass(slots=True)
class SourceOperationalState:
    status: str = "ready"
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    last_error_code: str | None = None
    _stable_status: str = "ready"
    _stable_error_code: str | None = None

    def start(self) -> None:
        self.status = "running"
        self.last_attempt_at = utcnow()
        self.last_error_code = None

    def success(self) -> None:
        self.status = "ok"
        self._stable_status = "ok"
        self.last_success_at = utcnow()
        self.last_error_code = None
        self._stable_error_code = None

    def failure(self, error_code: str | None = None, *, blocked: bool = False) -> None:
        outcome = "blocked" if blocked else "degraded"
        self.status = outcome
        self._stable_status = outcome
        self.last_failure_at = utcnow()
        self.last_error_code = error_code
        self._stable_error_code = error_code

    def cancelled(self) -> None:
        self.status = self._stable_status
        self.last_error_code = self._stable_error_code

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "last_attempt_at": self.last_attempt_at,
            "last_success_at": self.last_success_at,
            "last_failure_at": self.last_failure_at,
            "last_error_code": self.last_error_code,
        }


class _TrackedSourceAdapter:
    def __init__(self, adapter: SourceAdapter, state: SourceOperationalState) -> None:
        self._adapter = adapter
        self._state = state
        self.source_id = adapter.source_id
        self.intents = adapter.intents

    async def discover(self, request: CollectionRequest) -> list[SourceTask]:
        return await self._adapter.discover(request)

    async def fetch(self, task: SourceTask):
        self._state.start()
        try:
            return await self._adapter.fetch(task)
        except asyncio.CancelledError:
            self._state.cancelled()
            raise
        except Exception:
            self._state.failure("SOURCE_FETCH_ERROR")
            raise

    async def extract(self, task: SourceTask, fetched, request: CollectionRequest) -> SourceResult:
        try:
            return await self._adapter.extract(task, fetched, request)
        except asyncio.CancelledError:
            self._state.cancelled()
            raise
        except Exception:
            self._state.failure("SOURCE_EXTRACT_ERROR")
            raise

    async def normalize(self, result: SourceResult) -> SourceResult:
        try:
            normalized = await self._adapter.normalize(result)
        except asyncio.CancelledError:
            self._state.cancelled()
            raise
        except Exception:
            self._state.failure("SOURCE_NORMALIZE_ERROR")
            raise

        first_error = normalized.errors[0].code if normalized.errors else None
        if normalized.blocked:
            self._state.failure(first_error or "SOURCE_BLOCKED", blocked=True)
        elif normalized.partial or normalized.errors:
            self._state.failure(first_error or "SOURCE_PARTIAL")
        else:
            self._state.success()
        return normalized

    async def health(self) -> dict[str, object]:
        payload = dict(await self._adapter.health())
        adapter_status = payload.get("status")
        payload["adapter_status"] = adapter_status
        payload["status"] = self._state.status
        payload["operational"] = self._state.as_dict()
        return payload


class SourceRegistry:
    def __init__(self) -> None:
        self._sources: dict[str, SourceAdapter] = {}

    def register(self, adapter: SourceAdapter) -> None:
        if adapter.source_id in self._sources:
            raise ValueError(f"duplicate source_id: {adapter.source_id}")
        state = SourceOperationalState()
        self._sources[adapter.source_id] = _TrackedSourceAdapter(adapter, state)

    def get(self, source_id: str) -> SourceAdapter:
        return self._sources[source_id]

    def all(self) -> list[SourceAdapter]:
        return list(self._sources.values())

    def for_intents(self, intents: list[str]) -> list[SourceAdapter]:
        wanted = set(intents)
        return [
            source
            for source in self._sources.values()
            if source.intents & wanted or "*" in source.intents
        ]

    async def health(self, source_id: str) -> dict[str, object]:
        return await self.get(source_id).health()
