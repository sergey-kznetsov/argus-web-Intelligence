from __future__ import annotations

from argus.consumer_delivery import ConsumerDeliveryProjector
from argus.orchestrator.evidence_status import EvidenceStatusAdaptiveResearchOrchestrator
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

    tool_pack_execution_contract_version = "consumer-tool-pack/2"

    def __init__(self, *args, consumer_delivery=None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.consumer_delivery = consumer_delivery or ConsumerDeliveryProjector()

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
                },
            }
            await self.repository.update_collection(record)

        try:
            with activate_tool_pack(pack):
                await super()._run(collection_id)
        finally:
            self.consumer_delivery.release(collection_id)

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
