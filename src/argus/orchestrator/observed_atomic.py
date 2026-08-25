from __future__ import annotations

import time
from datetime import UTC, datetime

from argus.observability import OperationalMetrics
from argus.orchestrator.quality_atomic import QualityAwareAtomicCollectionOrchestrator


class ObservedAtomicCollectionOrchestrator(QualityAwareAtomicCollectionOrchestrator):
    """Quality-aware atomic orchestrator with bounded operational metrics."""

    def __init__(self, *args, metrics: OperationalMetrics, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.metrics = metrics

    async def submit(self, request):
        accepted = await super().submit(request)
        self.metrics.inc("collections_accepted_total", mode="embedded")
        return accepted

    async def _run(self, collection_id: str) -> None:
        record = await self.repository.get_collection(collection_id)
        if record is not None:
            queued_seconds = max(
                0.0,
                (datetime.now(UTC) - record.created_at).total_seconds(),
            )
            self.metrics.observe(
                "collection_queue_wait_seconds",
                queued_seconds,
                execution_role="worker" if not self.auto_execute else "embedded",
            )

        started = time.perf_counter()
        self.metrics.gauge("collections_running", 1.0, scope="process")
        try:
            await super()._run(collection_id)
        except BaseException:
            self.metrics.inc("collection_execution_errors_total")
            raise
        finally:
            duration = time.perf_counter() - started
            terminal = await self.repository.get_collection(collection_id)
            status = terminal.status.value if terminal is not None else "missing"
            self.metrics.observe("collection_duration_seconds", duration, status=status)
            self.metrics.inc("collections_finished_total", status=status)
            self.metrics.gauge("collections_running", 0.0, scope="process")

    async def _commit_task_success(
        self,
        record,
        *,
        observations,
        evidence,
        snapshots,
    ) -> None:
        started = time.perf_counter()
        try:
            await super()._commit_task_success(
                record,
                observations=observations,
                evidence=evidence,
                snapshots=snapshots,
            )
        except BaseException:
            self.metrics.inc("atomic_commit_errors_total")
            raise
        else:
            self.metrics.inc("observations_committed_total", len(observations))
            self.metrics.inc("evidence_committed_total", len(evidence))
            self.metrics.inc("snapshots_committed_total", len(snapshots))
        finally:
            self.metrics.observe(
                "atomic_commit_duration_seconds",
                time.perf_counter() - started,
            )
