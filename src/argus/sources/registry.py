from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime

from argus.contracts.models import CollectionRequest, utcnow
from argus.observability import OperationalMetrics
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
    def __init__(
        self,
        adapter: SourceAdapter,
        state: SourceOperationalState,
        metrics: OperationalMetrics | None,
    ) -> None:
        self._adapter = adapter
        self._state = state
        self._metrics = metrics
        self.source_id = adapter.source_id
        self.intents = adapter.intents

    async def discover(self, request: CollectionRequest) -> list[SourceTask]:
        started = time.perf_counter()
        try:
            tasks = await self._adapter.discover(request)
        except BaseException:
            if self._metrics is not None:
                self._metrics.inc(
                    "source_discovery_total",
                    source_id=self.source_id,
                    status="error",
                )
            raise
        else:
            if self._metrics is not None:
                self._metrics.inc(
                    "source_discovery_total",
                    source_id=self.source_id,
                    status="ok",
                )
                self._metrics.inc(
                    "source_discovered_tasks_total",
                    len(tasks),
                    source_id=self.source_id,
                )
            return tasks
        finally:
            if self._metrics is not None:
                self._metrics.observe(
                    "source_discovery_duration_seconds",
                    time.perf_counter() - started,
                    source_id=self.source_id,
                )

    async def fetch(self, task: SourceTask):
        self._state.start()
        started = time.perf_counter()
        runtime = "unknown"
        status = "error"
        try:
            fetched = await self._adapter.fetch(task)
            runtime = str(getattr(fetched, "runtime", None) or "unknown")[:80]
            status = "blocked" if bool(getattr(fetched, "blocked", False)) else "ok"
            return fetched
        except asyncio.CancelledError:
            status = "cancelled"
            self._state.cancelled()
            raise
        except Exception:
            self._state.failure("SOURCE_FETCH_ERROR")
            raise
        finally:
            if self._metrics is not None:
                self._metrics.inc(
                    "source_fetch_total",
                    source_id=self.source_id,
                    runtime=runtime,
                    status=status,
                )
                self._metrics.observe(
                    "source_fetch_duration_seconds",
                    time.perf_counter() - started,
                    source_id=self.source_id,
                    runtime=runtime,
                    status=status,
                )
                lowered = runtime.casefold()
                if "browser" in lowered or "agent" in lowered or "recipe" in lowered:
                    self._metrics.inc(
                        "runtime_escalation_total",
                        source_id=self.source_id,
                        runtime=runtime,
                        status=status,
                    )

    async def extract(self, task: SourceTask, fetched, request: CollectionRequest) -> SourceResult:
        started = time.perf_counter()
        status = "error"
        try:
            result = await self._adapter.extract(task, fetched, request)
            status = "ok"
            return result
        except asyncio.CancelledError:
            status = "cancelled"
            self._state.cancelled()
            raise
        except Exception:
            self._state.failure("SOURCE_EXTRACT_ERROR")
            raise
        finally:
            if self._metrics is not None:
                self._metrics.inc(
                    "source_extract_total",
                    source_id=self.source_id,
                    status=status,
                )
                self._metrics.observe(
                    "source_extract_duration_seconds",
                    time.perf_counter() - started,
                    source_id=self.source_id,
                    status=status,
                )

    async def normalize(self, result: SourceResult) -> SourceResult:
        started = time.perf_counter()
        try:
            normalized = await self._adapter.normalize(result)
        except asyncio.CancelledError:
            self._state.cancelled()
            if self._metrics is not None:
                self._metrics.inc(
                    "source_result_total",
                    source_id=self.source_id,
                    status="cancelled",
                )
            raise
        except Exception:
            self._state.failure("SOURCE_NORMALIZE_ERROR")
            if self._metrics is not None:
                self._metrics.inc(
                    "source_result_total",
                    source_id=self.source_id,
                    status="error",
                )
            raise
        finally:
            if self._metrics is not None:
                self._metrics.observe(
                    "source_normalize_duration_seconds",
                    time.perf_counter() - started,
                    source_id=self.source_id,
                )

        first_error = normalized.errors[0].code if normalized.errors else None
        if normalized.blocked:
            outcome = "blocked"
            self._state.failure(first_error or "SOURCE_BLOCKED", blocked=True)
        elif normalized.partial or normalized.errors:
            outcome = "partial"
            self._state.failure(first_error or "SOURCE_PARTIAL")
        else:
            outcome = "ok"
            self._state.success()

        if self._metrics is not None:
            self._metrics.inc(
                "source_result_total",
                source_id=self.source_id,
                status=outcome,
            )
            self._metrics.inc(
                "source_observations_total",
                len(normalized.observations),
                source_id=self.source_id,
            )
            self._metrics.inc(
                "source_evidence_total",
                len(normalized.evidence),
                source_id=self.source_id,
            )
            for error in normalized.errors:
                if error.retryable:
                    self._metrics.inc(
                        "source_retryable_errors_total",
                        source_id=self.source_id,
                        code=error.code,
                    )
        return normalized

    async def health(self) -> dict[str, object]:
        payload = dict(await self._adapter.health())
        adapter_status = payload.get("status")
        payload["adapter_status"] = adapter_status
        payload["status"] = self._state.status
        payload["operational"] = self._state.as_dict()
        return payload


class SourceRegistry:
    def __init__(self, metrics: OperationalMetrics | None = None) -> None:
        self._sources: dict[str, SourceAdapter] = {}
        self._metrics = metrics

    def register(self, adapter: SourceAdapter) -> None:
        if adapter.source_id in self._sources:
            raise ValueError(f"duplicate source_id: {adapter.source_id}")
        state = SourceOperationalState()
        self._sources[adapter.source_id] = _TrackedSourceAdapter(
            adapter,
            state,
            self._metrics,
        )

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
