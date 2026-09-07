from __future__ import annotations

from argus.consumer_delivery import ConsumerDeliveryProjector
from argus.orchestrator.evidence_status import EvidenceStatusAdaptiveResearchOrchestrator
from argus.research.source_contours import SourceContourResearchPlanner
from argus.toolpacks import (
    activate_tool_pack,
    active_tool_pack,
    resolved_tool_pack_from_request,
)


class ToolPackAwareEvidenceStatusAdaptiveResearchOrchestrator(
    EvidenceStatusAdaptiveResearchOrchestrator
):
    """Run every collection inside its versioned consumer tool-pack boundary.

    ``contextvars`` keeps the active pack scoped to the current async collection task. The
    SourceRegistry consumes that context both when selecting initial adapters and when a
    later discovery/child task asks for an adapter, so concurrent consumers cannot leak
    source tooling into one another.

    Consumer-specific delivery semantics are also selected from the ToolPack. ARGUS Core
    never branches on consumer IDs: Kraken's broad stream is one policy, while Janus,
    Historical and future consumers can keep different result policies.
    """

    tool_pack_execution_contract_version = "consumer-tool-pack/3"

    def __init__(
        self,
        *args,
        consumer_delivery=None,
        source_contour_planner: SourceContourResearchPlanner | None = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.consumer_delivery = consumer_delivery or ConsumerDeliveryProjector()
        self.source_contour_planner = source_contour_planner or SourceContourResearchPlanner()

    async def _run(self, collection_id: str) -> None:
        record = await self.repository.get_collection(collection_id)
        if record is None:
            await super()._run(collection_id)
            return

        pack = resolved_tool_pack_from_request(record.request)
        if pack is not None:
            record.checkpoint = {
                **record.checkpoint,
                "consumer_execution_contract": {
                    "version": self.tool_pack_execution_contract_version,
                    "consumer_id": record.request.consumer,
                    "consumer_profile_version": record.request.consumer_profile_version,
                    "capability": record.request.capability,
                    "requested_facts": list(record.request.requested_facts),
                    "tool_pack_id": pack.tool_pack_id,
                    "tool_pack_version": pack.version,
                    "planner_policy": pack.planner_policy,
                    "recipe_namespace": pack.recipe_namespace,
                    "extractor_policy": pack.extractor_policy,
                    "result_delivery_policy": pack.result_delivery_policy,
                    "result_dedup_policy": pack.result_dedup_policy,
                    "shared_tools": list(pack.shared_tools),
                    "source_contour_policy": (
                        self.source_contour_planner.version
                        if self.source_contour_planner.supports_policy(pack.planner_policy)
                        else None
                    ),
                },
            }
            await self.repository.update_collection(record)

        try:
            with activate_tool_pack(pack):
                await super()._run(collection_id)
        finally:
            self.consumer_delivery.release(collection_id)

    async def _discover_uncovered_intents(
        self,
        record,
        pending,
        uncovered_intents,
    ):
        pending = await self._discover_source_contours(record, pending)
        return await super()._discover_uncovered_intents(
            record,
            pending,
            uncovered_intents,
        )

    async def _discover_source_contours(self, record, pending):
        pack = active_tool_pack()
        if (
            pack is None
            or self.discovery is None
            or not self.source_contour_planner.supports_policy(pack.planner_policy)
            or record.checkpoint.get("source_contours_complete") is True
        ):
            return pending

        plans = self.source_contour_planner.plans(
            record.request,
            planner_policy=pack.planner_policy,
        )
        states_raw = record.checkpoint.get("source_contours")
        states = dict(states_raw) if isinstance(states_raw, dict) else {}
        all_queries = list(record.checkpoint.get("source_contour_queries", []))

        record.stage = "discovery:source_contours"
        record.updated_at = __import__("argus.orchestrator.service", fromlist=["now"]).now()
        await self.repository.update_collection(record)

        for plan in plans:
            previous = states.get(plan.contour_id)
            if isinstance(previous, dict) and previous.get("attempted") is True:
                continue

            constraints = record.request.constraints.model_copy(
                update={"max_pages": max(1, int(plan.max_destinations))}
            )
            contour_request = record.request.model_copy(update={"constraints": constraints})
            outcome = await self.discovery.discover(list(plan.queries), contour_request)

            tagged_tasks = []
            for task in outcome.tasks:
                task.metadata["source_contour"] = plan.contour_id
                task.metadata["source_contour_version"] = self.source_contour_planner.version
                task.metadata["source_contour_description"] = plan.description
                task.metadata["source_contour_priority"] = plan.priority
                tagged_tasks.append(task)
            pending = self._merge_tasks(pending, tagged_tasks, record.collection_id)

            for query in plan.queries:
                if query not in all_queries:
                    all_queries.append(query)

            if tagged_tasks:
                status = "discovered"
            elif outcome.blocked:
                status = "blocked"
            elif any(error.code != "DISCOVERY_NO_RESULTS" for error in outcome.errors):
                status = "degraded"
            else:
                status = "no_results"

            states[plan.contour_id] = {
                "attempted": True,
                "status": status,
                "priority": plan.priority,
                "description": plan.description,
                "queries": list(plan.queries),
                "providers_attempted": list(outcome.providers_attempted),
                "candidates_seen": outcome.candidates_seen,
                "valid_destinations": outcome.valid_destinations,
                "destinations_selected": len(tagged_tasks),
                "blocked": outcome.blocked,
                "stop_reason": outcome.stop_reason,
                "error_codes": [error.code for error in outcome.errors],
            }
            record.checkpoint = {
                **record.checkpoint,
                "source_contour_version": self.source_contour_planner.version,
                "source_contours": states,
                "source_contour_queries": all_queries,
                "pending_tasks": [self._task_dict(task) for task in pending],
            }
            record.updated_at = __import__("argus.orchestrator.service", fromlist=["now"]).now()
            await self.repository.update_collection(record)

        record.checkpoint = {
            **record.checkpoint,
            "source_contour_version": self.source_contour_planner.version,
            "source_contours": states,
            "source_contour_queries": all_queries,
            "source_contours_complete": True,
            "pending_tasks": [self._task_dict(task) for task in pending],
        }
        record.updated_at = __import__("argus.orchestrator.service", fromlist=["now"]).now()
        await self.repository.update_collection(record)
        return pending

    async def _commit_task_success(
        self,
        record,
        *,
        observations,
        evidence,
        snapshots,
    ) -> None:
        pack = active_tool_pack()
        projected_observations, projected_evidence, stats = (
            await self.consumer_delivery.project_task_result(
                self.repository,
                collection_id=record.collection_id,
                pack=pack,
                observations=list(observations),
                evidence=list(evidence),
            )
        )
        previous = record.checkpoint.get("consumer_result_delivery")
        previous = previous if isinstance(previous, dict) else {}
        record.checkpoint = {
            **record.checkpoint,
            "consumer_result_delivery": {
                "version": stats.get("version"),
                "policy": stats.get("policy"),
                "dedup_policy": stats.get("dedup_policy"),
                "semantic_filtering_applied": stats.get(
                    "semantic_filtering_applied", False
                ),
                "observations_input": int(previous.get("observations_input", 0) or 0)
                + int(stats.get("observations_input", 0) or 0),
                "observations_output": int(previous.get("observations_output", 0) or 0)
                + int(stats.get("observations_output", 0) or 0),
                "duplicates_collapsed": int(previous.get("duplicates_collapsed", 0) or 0)
                + int(stats.get("duplicates_collapsed", 0) or 0),
            },
        }
        await super()._commit_task_success(
            record,
            observations=projected_observations,
            evidence=projected_evidence,
            snapshots=snapshots,
        )
