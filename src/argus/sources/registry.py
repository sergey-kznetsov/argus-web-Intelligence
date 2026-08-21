from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from argus.contracts.models import utcnow
from argus.sources.base import SourceAdapter


@dataclass(slots=True)
class SourceOperationalState:
    status: str = "ready"
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    last_error_code: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "last_attempt_at": self.last_attempt_at,
            "last_success_at": self.last_success_at,
            "last_failure_at": self.last_failure_at,
            "last_error_code": self.last_error_code,
        }


class SourceRegistry:
    def __init__(self) -> None:
        self._sources: dict[str, SourceAdapter] = {}
        self._operational: dict[str, SourceOperationalState] = {}

    def register(self, adapter: SourceAdapter) -> None:
        if adapter.source_id in self._sources:
            raise ValueError(f"duplicate source_id: {adapter.source_id}")
        self._sources[adapter.source_id] = adapter
        self._operational[adapter.source_id] = SourceOperationalState()

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

    def mark_attempt(self, source_id: str) -> None:
        state = self._operational[source_id]
        state.status = "running"
        state.last_attempt_at = utcnow()
        state.last_error_code = None

    def mark_result(self, source_id: str, status: str, error_code: str | None = None) -> None:
        state = self._operational[source_id]
        timestamp = utcnow()
        if status == "ok":
            state.status = "ok"
            state.last_success_at = timestamp
            state.last_error_code = None
            return
        state.status = "blocked" if status == "blocked" else "degraded"
        state.last_failure_at = timestamp
        state.last_error_code = error_code

    async def health(self, source_id: str) -> dict[str, object]:
        adapter = self.get(source_id)
        payload = dict(await adapter.health())
        adapter_status = payload.get("status")
        state = self._operational[source_id]
        payload["adapter_status"] = adapter_status
        payload["status"] = state.status
        payload["operational"] = state.as_dict()
        return payload
