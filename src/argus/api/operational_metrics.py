from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from fastapi import Depends, FastAPI

from argus.config import Settings
from argus.services import ServiceContainer


def register_operational_metrics_endpoint(
    app: FastAPI,
    *,
    settings: Settings,
    services: ServiceContainer,
    repository: Any,
    require_bearer: Callable[..., Any],
) -> None:
    """Register a bounded, authenticated operational snapshot endpoint."""

    @app.get("/v1/operations/metrics", dependencies=[Depends(require_bearer)])
    async def operational_metrics() -> dict[str, object]:
        queue_payload: dict[str, object] | None = None
        queue_reader = getattr(repository, "queue_metrics", None)
        if settings.execution_role == "api" and callable(queue_reader):
            started = time.perf_counter()
            try:
                queue = await queue_reader(
                    worker_max_age_seconds=settings.worker_health_max_age_seconds
                )
            except Exception:
                services.metrics.inc("operations_queue_reads_total", status="error")
                queue_payload = {"status": "error"}
            else:
                services.metrics.inc("operations_queue_reads_total", status="ok")
                queue_payload = queue.as_dict()
            finally:
                services.metrics.observe(
                    "db_operation_duration_seconds",
                    time.perf_counter() - started,
                    operation="queue_metrics",
                )

        return {
            "process": services.metrics.snapshot(),
            "queue": queue_payload,
            "execution_role": settings.execution_role,
            "storage_backend": settings.storage_backend,
            "exporters": {
                "prometheus": False,
                "opentelemetry": False,
                "built_in_json": True,
            },
        }
