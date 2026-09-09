from __future__ import annotations

from argus.orchestrator.service import now
from argus.orchestrator.toolpack_aware import (
    ToolPackAwareEvidenceStatusAdaptiveResearchOrchestrator,
)
from argus.research.lane_coverage import build_research_lane_coverage
from argus.toolpacks import resolved_tool_pack_from_request


class MandatoryCoverageToolPackOrchestrator(
    ToolPackAwareEvidenceStatusAdaptiveResearchOrchestrator
):
    """Keep mandatory urban coverage complete while bounding optional deep research.

    Source-family and public-map lanes are mandatory for ``urban_signals`` and therefore
    cannot share the old collection-wide page/time budget. During that serial phase ARGUS
    uses only an emergency ceiling while each source lane remains independently bounded.

    Once all source contours and public-map providers have been attempted, the collection
    transitions to a small, fresh optional-research budget. The generic per-intent discovery
    pass is intentionally skipped for this policy because ``general_web`` is already the
    seventh mandatory contour; running both duplicated discovery and allowed blocked or slow
    sites to dominate latency without adding a new source family.

    Other tool packs keep the normal collection-budget semantics unchanged.
    """

    mandatory_coverage_version = "mandatory-coverage/2"
    emergency_max_pages = 500
    emergency_max_duration_seconds = 7_200.0
    post_mandatory_optional_pages = 24
    post_mandatory_optional_duration_seconds = 120.0

    async def _run(self, collection_id: str) -> None:
        record = await self.repository.get_collection(collection_id)
        if record is not None:
            await self._apply_urban_signal_execution_guard(record)
        await super()._run(collection_id)

    async def _discover_uncovered_intents(
        self,
        record,
        pending,
        uncovered_intents,
    ):
        """Run mandatory urban lanes once, then hand only bounded work to collection crawl."""

        pack = resolved_tool_pack_from_request(record.request)
        if pack is None or pack.planner_policy != "urban_signals":
            return await super()._discover_uncovered_intents(
                record,
                pending,
                uncovered_intents,
            )

        pending = await self._run_pre_contour_street_inventory(record, pending)
        pending = await self._run_serial_source_contours(record, pending)
        pending = await self._run_serial_public_maps(record, pending)
        await self._activate_post_mandatory_budget(record)

        # ``general_web`` already provides the catch-all discovery family. Re-running the
        # base per-intent discovery here duplicates the same open-web candidates and was the
        # source of long generic_web/RSS queues after mandatory coverage had completed.
        return (
            pending,
            list(record.checkpoint.get("discovery_queries", [])),
            list(record.checkpoint.get("discovery_providers", [])),
            bool(record.checkpoint.get("discovery_blocked", False)),
        )

    async def _apply_urban_signal_execution_guard(self, record) -> bool:
        pack = resolved_tool_pack_from_request(record.request)
        if pack is None or pack.planner_policy != "urban_signals":
            return False

        existing_guard = record.checkpoint.get("mandatory_coverage")
        existing_guard = existing_guard if isinstance(existing_guard, dict) else {}
        if existing_guard.get("mandatory_complete") is True:
            # Recovered collections that already crossed the mandatory boundary must keep
            # their bounded post-mandatory budget instead of being expanded back to 2 hours.
            return False

        constraints = record.request.constraints
        requested_max_pages = int(
            existing_guard.get("requested_max_pages", constraints.max_pages)
        )
        requested_max_duration_seconds = float(
            existing_guard.get(
                "requested_max_duration_seconds",
                constraints.max_duration_seconds,
            )
        )
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
                **existing_guard,
                "version": self.mandatory_coverage_version,
                "policy": "all_source_contours_then_all_public_maps",
                "phase": "mandatory",
                "mandatory_complete": False,
                "collection_limits_semantics": "mandatory_emergency_optional_bounded",
                "requested_max_pages": requested_max_pages,
                "requested_max_duration_seconds": requested_max_duration_seconds,
                "effective_emergency_max_pages": self.emergency_max_pages,
                "effective_emergency_max_duration_seconds": (
                    self.emergency_max_duration_seconds
                ),
                "post_mandatory_optional_pages": self.post_mandatory_optional_pages,
                "post_mandatory_optional_duration_seconds": (
                    self.post_mandatory_optional_duration_seconds
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

    async def _activate_post_mandatory_budget(self, record) -> bool:
        pack = resolved_tool_pack_from_request(record.request)
        if pack is None or pack.planner_policy != "urban_signals":
            return False
        if record.checkpoint.get("source_contours_complete") is not True:
            return False
        if record.checkpoint.get("serial_public_map_complete") is not True:
            return False

        raw_guard = record.checkpoint.get("mandatory_coverage")
        guard = dict(raw_guard) if isinstance(raw_guard, dict) else {}
        if guard.get("mandatory_complete") is True:
            return False

        observations = await self.repository.list_observations(record.collection_id)
        evidence = await self.repository.list_evidence(record.collection_id)
        research_lane_coverage = build_research_lane_coverage(
            record,
            observations,
            evidence,
        )

        visited = record.checkpoint.get("visited", [])
        visited_count = len(visited) if isinstance(visited, list) else 0
        total_page_ceiling = min(
            self.emergency_max_pages,
            visited_count + self.post_mandatory_optional_pages,
        )
        effective = record.request.constraints.model_copy(
            update={
                "max_pages": max(1, total_page_ceiling),
                "max_duration_seconds": self.post_mandatory_optional_duration_seconds,
            }
        )
        record.request = record.request.model_copy(update={"constraints": effective})
        optional_started_at = now()
        record.checkpoint = {
            **record.checkpoint,
            "execution_budget_started_at": optional_started_at.isoformat(),
            "research_lane_coverage": research_lane_coverage,
            "mandatory_coverage": {
                **guard,
                "version": self.mandatory_coverage_version,
                "phase": "optional",
                "mandatory_complete": True,
                "mandatory_processed_pages": visited_count,
                "effective_post_mandatory_max_pages": int(effective.max_pages),
                "post_mandatory_optional_pages": self.post_mandatory_optional_pages,
                "post_mandatory_optional_duration_seconds": (
                    self.post_mandatory_optional_duration_seconds
                ),
                "optional_started_at": optional_started_at.isoformat(),
            },
        }
        record.updated_at = optional_started_at
        await self.repository.update_collection(record)
        return True

    @classmethod
    def _execution_budget_exhausted(cls, record) -> bool:
        pack = resolved_tool_pack_from_request(record.request)
        raw_guard = record.checkpoint.get("mandatory_coverage")
        guard = raw_guard if isinstance(raw_guard, dict) else {}
        if (
            pack is not None
            and pack.planner_policy == "urban_signals"
            and guard.get("mandatory_complete") is not True
        ):
            # Mandatory serial lanes must never be skipped because an earlier lane used a
            # collection-wide page budget. Per-lane page limits and per-task timeouts still
            # bound the actual work.
            return False
        return ToolPackAwareEvidenceStatusAdaptiveResearchOrchestrator._execution_budget_exhausted(
            record
        )