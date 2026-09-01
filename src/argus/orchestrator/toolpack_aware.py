from __future__ import annotations

from argus.orchestrator.evidence_status import EvidenceStatusAdaptiveResearchOrchestrator
from argus.toolpacks import activate_tool_pack, resolved_tool_pack_from_request


class ToolPackAwareEvidenceStatusAdaptiveResearchOrchestrator(
    EvidenceStatusAdaptiveResearchOrchestrator
):
    """Run every collection inside its versioned consumer tool-pack boundary.

    ``contextvars`` keeps the active pack scoped to the current async collection task. The
    SourceRegistry consumes that context both when selecting initial adapters and when a
    later discovery/child task asks for an adapter, so concurrent consumers cannot leak
    source tooling into one another.
    """

    tool_pack_execution_contract_version = "consumer-tool-pack/1"

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
                    "shared_tools": list(pack.shared_tools),
                },
            }
            await self.repository.update_collection(record)

        with activate_tool_pack(pack):
            await super()._run(collection_id)
