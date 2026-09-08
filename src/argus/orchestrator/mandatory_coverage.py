from __future__ import annotations

from argus.orchestrator.service import now
from argus.orchestrator.toolpack_aware import (
    ToolPackAwareEvidenceStatusAdaptiveResearchOrchestrator,
)
from argus.toolpacks import resolved_tool_pack_from_request


class MandatoryCoverageToolPackOrchestrator(
    ToolPackAwareEvidenceStatusAdaptiveResearchOrchestrator
):
    """Treat collection-wide limits as emergency guards for urban-signal research.

    Kraken's source-family contract requires every configured public-source contour and
    every configured public-map provider to receive a serial attempt. The historical
    collection defaults (30 pages / 240 seconds) were small enough to terminate that
    mandatory sequence after an early high-yield source. For ``urban_signals`` requests we
    therefore raise only the collection-wide guard to the schema ceiling while retaining
    the existing bounded source lanes, source-task timeout, depth limit, rate controls,
    deduplication, circuit breakers and provider-specific navigation limits.

    Other consumer tool packs keep their request constraints unchanged.
    """

    mandatory_coverage_version = "mandatory-coverage/1"
    emergency_max_pages = 500
    emergency_max_duration_seconds = 7_200.0

    async def _run(self, collection_id: str) -> None:
        record = await self.repository.get_collection(collection_id)
        if record is not None:
            await self._apply_urban_signal_execution_guard(record)
        await super()._run(collection_id)

    async def _apply_urban_signal_execution_guard(self, record) -> bool:
        pack = resolved_tool_pack_from_request(record.request)
        if pack is None or pack.planner_policy != "urban_signals":
            return False

        constraints = record.request.constraints
        requested_max_pages = int(constraints.max_pages)
        requested_max_duration_seconds = float(constraints.max_duration_seconds)
        effective = constraints.model_copy(
            update={
                "max_pages": self.emergency_max_pages,
                "max_duration_seconds": self.emergency_max_duration_seconds,
            }
        )
        record.request = record.request.model_copy(update={"constraints": effective})

        contour_ids = [
            str(item.get("contour_id") or "").strip()
            for item in self.source_contour_planner.catalog(pack.planner_policy)
            if str(item.get("contour_id") or "").strip()
        ]
        map_provider_ids = (
            [item.source_id for item in self.public_map_source_planner.sources]
            if self.public_map_source_planner is not None
            else []
        )
        record.checkpoint = {
            **record.checkpoint,
            "mandatory_coverage": {
                "version": self.mandatory_coverage_version,
                "policy": "all_source_contours_then_all_public_maps",
                "collection_limits_semantics": "emergency_guard_only",
                "requested_max_pages": requested_max_pages,
                "requested_max_duration_seconds": requested_max_duration_seconds,
                "effective_emergency_max_pages": self.emergency_max_pages,
                "effective_emergency_max_duration_seconds": (
                    self.emergency_max_duration_seconds
                ),
                "source_contours": contour_ids,
                "public_map_providers": map_provider_ids,
                "source_task_timeout_seconds": self.source_task_timeout_seconds,
                "max_depth": int(effective.max_depth),
            },
        }
        record.updated_at = now()
        await self.repository.update_collection(record)
        return True

    @classmethod
    def _execution_budget_exhausted(cls, record) -> bool:
        pack = resolved_tool_pack_from_request(record.request)
        if pack is not None and pack.planner_policy == "urban_signals":
            # Mandatory serial lanes must never be skipped because an earlier lane used a
            # collection-wide page budget. Per-lane page limits and per-task timeouts still
            # bound the actual work.
            return False
        return ToolPackAwareEvidenceStatusAdaptiveResearchOrchestrator._execution_budget_exhausted(
            record
        )
