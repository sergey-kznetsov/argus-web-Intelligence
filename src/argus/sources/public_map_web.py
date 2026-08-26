from __future__ import annotations

from argus.contracts.models import CollectionRequest
from argus.normalization.public_map_provenance import classify_public_map_url
from argus.sources.base import SourceResult, SourceTask
from argus.sources.historical_web import HistoricalTimelineWebAdapter


class PublicMapProvenanceWebAdapter(HistoricalTimelineWebAdapter):
    """Attach public-map provenance and semantically escalate unresolved review goals.

    Public map pages are frequently interactive applications. A technically successful
    FAST/BROWSER response is not enough when the requested factual goal is reviews but
    no source-declared review facts were extracted. In that narrow case the adapter may
    invoke the normal AGENT -> verified browser replay lifecycle once. Agent output is
    still never accepted as Evidence directly.
    """

    semantic_escalation_version = "public-map-goal-escalation/1"

    async def extract(
        self,
        task: SourceTask,
        fetched,
        request: CollectionRequest,
    ) -> SourceResult:
        result = await super().extract(task, fetched, request)

        if self._should_semantically_escalate(task, fetched, result):
            task.metadata["semantic_agent_retry_attempted"] = True
            task.metadata["semantic_agent_retry_reason"] = "review_goal_without_review_fact"
            guided = await self._agent_guided_fetch(task, context_fetch=fetched)
            if guided is not None and not guided.blocked:
                guided_result = await super().extract(task, guided, request)
                if self._review_fact_count(guided_result) > self._review_fact_count(result):
                    result = guided_result
                    task.metadata["semantic_agent_retry_accepted"] = True
                else:
                    task.metadata["semantic_agent_retry_accepted"] = False
            else:
                task.metadata["semantic_agent_retry_accepted"] = False

        self._attach_public_map_provenance(result, task)
        return result

    def _should_semantically_escalate(
        self,
        task: SourceTask,
        fetched,
        result: SourceResult,
    ) -> bool:
        if self.agent is None or task.metadata.get("semantic_agent_retry_attempted"):
            return False
        if fetched.blocked or result.blocked:
            return False
        if classify_public_map_url(str(fetched.final_url)) is None:
            return False
        goals = set(self._research_goals(task))
        if "reviews" not in goals:
            return False
        return self._review_fact_count(result) == 0

    @staticmethod
    def _review_fact_count(result: SourceResult) -> int:
        return sum(1 for item in result.observations if item.entity_type == "review")

    def _attach_public_map_provenance(
        self,
        result: SourceResult,
        task: SourceTask,
    ) -> None:
        escalation = {
            "version": self.semantic_escalation_version,
            "attempted": bool(task.metadata.get("semantic_agent_retry_attempted")),
            "accepted": bool(task.metadata.get("semantic_agent_retry_accepted")),
            "reason": task.metadata.get("semantic_agent_retry_reason"),
            "agent_output_is_evidence": False,
        }
        for observation in result.observations:
            provenance = classify_public_map_url(observation.url)
            if provenance is None:
                continue
            observation.provenance["public_map_source"] = dict(provenance)
            observation.provenance["public_map_semantic_escalation"] = dict(escalation)
            observation.quality["public_map_source_identified"] = True

        for evidence in result.evidence:
            provenance = classify_public_map_url(evidence.source.url)
            if provenance is None:
                continue
            evidence.metadata["public_map_source"] = dict(provenance)
            evidence.metadata["public_map_semantic_escalation"] = dict(escalation)

    async def health(self) -> dict[str, object]:
        payload = dict(await super().health())
        payload["public_map_web_provenance"] = {
            "enabled": True,
            "providers": ["yandex_maps_web", "2gis_web", "google_maps_web"],
            "classification_basis": "url_host_path",
            "content_inference": False,
            "paid_api": False,
            "semantic_goal_escalation": {
                "version": self.semantic_escalation_version,
                "review_goal": True,
                "requires_agent_backend": True,
                "agent_output_is_evidence": False,
                "verified_browser_replay": True,
            },
        }
        return payload
