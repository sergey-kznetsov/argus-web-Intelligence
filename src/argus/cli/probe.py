from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from fastapi.encoders import jsonable_encoder

from argus.bootstrap import build_services
from argus.config import Settings
from argus.contracts.models import CollectionRequest, CollectionStatus

_TERMINAL_STATUSES = {
    CollectionStatus.COMPLETED,
    CollectionStatus.PARTIAL,
    CollectionStatus.BLOCKED,
    CollectionStatus.FAILED,
    CollectionStatus.CANCELLED,
}


async def run_embedded_probe(
    settings: Settings,
    request: CollectionRequest,
    *,
    timeout_seconds: float = 300.0,
    poll_interval_seconds: float = 0.25,
) -> dict[str, Any]:
    """Run one real ARGUS collection in-process without Geo Analyzer.

    The probe intentionally uses the production service graph and orchestrator. The only
    deployment substitutions are embedded execution and local SQLite storage, so source
    discovery, crawling, extraction, provenance, evidence and recovery code stay the same.
    """

    if settings.execution_role != "embedded":
        raise ValueError("standalone probe requires execution_role=embedded")
    if settings.storage_backend != "sqlite":
        raise ValueError("standalone probe requires storage_backend=sqlite")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if poll_interval_seconds <= 0:
        raise ValueError("poll_interval_seconds must be positive")

    services = build_services(settings)
    started = False
    started_at = time.perf_counter()
    try:
        await services.start()
        started = True
        accepted = await services.orchestrator.submit(request)
        collection_id = accepted.collection_id
        deadline = time.monotonic() + timeout_seconds

        while True:
            record = await services.repository.get_collection(collection_id)
            if record is None:
                raise RuntimeError("standalone collection disappeared from storage")
            if record.status in _TERMINAL_STATUSES:
                break
            if time.monotonic() >= deadline:
                await services.orchestrator.cancel(collection_id)
                raise TimeoutError(
                    f"standalone ARGUS probe exceeded {timeout_seconds:g} seconds"
                )
            await asyncio.sleep(poll_interval_seconds)

        result = await services.orchestrator.result(collection_id)
        if result is None:
            raise RuntimeError("standalone collection result is unavailable")

        source_health: dict[str, object] = {}
        for adapter in services.registry.all():
            try:
                source_health[adapter.source_id] = await adapter.health()
            except Exception as exc:
                source_health[adapter.source_id] = {
                    "status": "health_error",
                    "error_type": type(exc).__name__,
                }

        elapsed = max(0.0, time.perf_counter() - started_at)
        return jsonable_encoder(
            {
                "probe": {
                    "mode": "embedded",
                    "storage_backend": "sqlite",
                    "database_path": str(Path(settings.db_path).resolve()),
                    "elapsed_seconds": round(elapsed, 3),
                },
                "request": request,
                "collection": record,
                "result": result,
                "source_health": source_health,
                "metrics": services.metrics.snapshot(),
            }
        )
    finally:
        if started:
            await services.shutdown()


def render_probe_summary(
    report: dict[str, Any],
    *,
    preview_items: int = 10,
    preview_chars: int = 500,
) -> str:
    """Render a bounded human-readable view while the JSON report keeps full evidence."""

    preview_items = max(0, preview_items)
    preview_chars = max(0, preview_chars)
    result = report.get("result") if isinstance(report, dict) else None
    collection = report.get("collection") if isinstance(report, dict) else None
    probe = report.get("probe") if isinstance(report, dict) else None
    if not isinstance(result, dict) or not isinstance(collection, dict):
        return "ARGUS probe report is incomplete"

    observations = result.get("observations")
    evidence = result.get("evidence")
    coverage = result.get("coverage")
    errors = result.get("errors")
    observations = observations if isinstance(observations, list) else []
    evidence = evidence if isinstance(evidence, list) else []
    coverage = coverage if isinstance(coverage, list) else []
    errors = errors if isinstance(errors, list) else []

    lines = [
        f"Collection: {result.get('collection_id', '<unknown>')}",
        f"Status: {result.get('status', '<unknown>')}",
        f"Stage: {collection.get('stage') or '-'}",
        f"Observations: {len(observations)}",
        f"Evidence: {len(evidence)}",
        f"Errors: {len(errors)}",
    ]
    if isinstance(probe, dict) and probe.get("elapsed_seconds") is not None:
        lines.append(f"Elapsed: {probe['elapsed_seconds']} s")

    if coverage:
        lines.append("")
        lines.append("Source coverage:")
        for item in coverage:
            if not isinstance(item, dict):
                continue
            lines.append(
                "- "
                f"{item.get('source_id', '?')}: {item.get('status', '?')}; "
                f"observations={item.get('observations', 0)}; "
                f"blocked={bool(item.get('blocked', False))}"
            )

    if errors:
        lines.append("")
        lines.append("Errors:")
        for item in errors[:preview_items or len(errors)]:
            if not isinstance(item, dict):
                continue
            message = _preview(str(item.get("message") or ""), preview_chars)
            lines.append(
                f"- {item.get('code', '?')} [{item.get('source_id') or 'collection'}]: {message}"
            )

    if preview_items and observations:
        lines.append("")
        lines.append("Observation preview:")
        for index, item in enumerate(observations[:preview_items], start=1):
            if not isinstance(item, dict):
                continue
            label = item.get("title") or item.get("entity_type") or "observation"
            lines.append(
                f"[{index}] {item.get('source_kind', '?')} | {label} | {item.get('url', '')}"
            )
            text = _preview(str(item.get("text") or ""), preview_chars)
            if text:
                lines.append(f"    {text}")

    if preview_items and evidence:
        lines.append("")
        lines.append("Evidence preview:")
        for index, item in enumerate(evidence[:preview_items], start=1):
            if not isinstance(item, dict):
                continue
            source = item.get("source")
            source = source if isinstance(source, dict) else {}
            lines.append(
                f"[{index}] {item.get('type', '?')} | "
                f"{source.get('provider', '?')} | {source.get('url', '')}"
            )
            text = _preview(str(item.get("text") or ""), preview_chars)
            if text:
                lines.append(f"    {text}")

    return "\n".join(lines)


def _preview(value: str, limit: int) -> str:
    compact = " ".join(value.split())
    if limit <= 0 or len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 1)].rstrip() + "…"
