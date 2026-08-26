from __future__ import annotations

from argus.contracts.models import CollectionRequest
from argus.normalization.public_map_provenance import (
    classify_public_map_url,
    preferred_public_map_review_url,
)
from argus.research.coverage import IntentCoverageEvaluator
from argus.security.urls import UnsafeUrlError
from argus.sources.base import SourceResult, SourceTask
from argus.sources.historical_web import HistoricalTimelineWebAdapter


class PublicMapProvenanceWebAdapter(HistoricalTimelineWebAdapter):
    """Attach public-map provenance and resolve semantic goals without paid APIs.

    Public map pages are frequently interactive applications. A technically successful
    FAST/BROWSER response is not enough when the requested factual goal has not actually
    been evidenced. ARGUS therefore evaluates source-backed intent coverage, prefers a
    deterministic public review view when the provider exposes one, and only then may use
    the normal AGENT -> verified browser replay lifecycle. Agent output is never Evidence.
    """

    semantic_escalation_version = "public-map-goal-escalation/3"
    semantic_escalation_goals = frozenset(
        {"reviews", "comments", "discussions", "complaints"}
    )
    deterministic_review_view_goals = semantic_escalation_goals
    intent_coverage = IntentCoverageEvaluator()

    async def extract(
        self,
        task: SourceTask,
        fetched,
        request: CollectionRequest,
    ) -> SourceResult:
        result = await super().extract(task, fetched, request)
        result = await self._annotate_semantic_evidence(request, result)
        goals = self._semantic_goals(task)

        if goals and self._semantic_goal_fact_count(result, goals) == 0:
            result = await self._try_deterministic_review_view(
                task,
                fetched,
                request,
                result,
                goals,
            )

        if self._should_semantically_escalate(task, fetched, result):
            task.metadata["semantic_agent_retry_attempted"] = True
            task.metadata["semantic_agent_retry_goals"] = goals
            task.metadata["semantic_agent_retry_reason"] = (
                "review_goal_without_review_fact"
                if goals == ["reviews"]
                else "semantic_goal_without_evidence"
            )
            before_score = self._semantic_goal_fact_count(result, goals)
            guided = await self._agent_guided_fetch(task, context_fetch=fetched)
            if guided is not None and not guided.blocked:
                guided_result = await super().extract(task, guided, request)
                guided_result = await self._annotate_semantic_evidence(request, guided_result)
                after_score = self._semantic_goal_fact_count(guided_result, goals)
                if after_score > before_score:
                    result = guided_result
                    task.metadata["semantic_agent_retry_accepted"] = True
                else:
                    task.metadata["semantic_agent_retry_accepted"] = False
            else:
                task.metadata["semantic_agent_retry_accepted"] = False

        self._attach_public_map_provenance(result, task)
        return result

    async def _try_deterministic_review_view(
        self,
        task: SourceTask,
        fetched,
        request: CollectionRequest,
        result: SourceResult,
        goals: list[str],
    ) -> SourceResult:
        if fetched.blocked or result.blocked:
            return result
        if not set(goals).intersection(self.deterministic_review_view_goals):
            return result
        candidate_url = preferred_public_map_review_url(str(fetched.final_url))
        if candidate_url is None:
            return result

        task.metadata["public_map_review_view_attempted"] = True
        task.metadata["public_map_review_view_url"] = candidate_url
        task.metadata["public_map_review_view_basis"] = "provider_public_url_shape"
        before_score = self._semantic_goal_fact_count(result, goals)
        try:
            candidate_fetched = await self.browser.fetch(candidate_url)
        except UnsafeUrlError:
            raise
        except Exception as exc:
            task.metadata["public_map_review_view_accepted"] = False
            task.metadata["public_map_review_view_error_type"] = type(exc).__name__
            return result

        if candidate_fetched.blocked:
            task.metadata["public_map_review_view_accepted"] = False
            task.metadata["public_map_review_view_blocked"] = True
            task.metadata["semantic_agent_retry_suppressed"] = "review_view_blocked"
            return result

        candidate_result = await super().extract(task, candidate_fetched, request)
        candidate_result = await self._annotate_semantic_evidence(request, candidate_result)
        after_score = self._semantic_goal_fact_count(candidate_result, goals)
        accepted = after_score > before_score
        task.metadata["public_map_review_view_accepted"] = accepted
        task.metadata["public_map_review_view_final_url"] = candidate_fetched.final_url
        if accepted:
            return candidate_result
        return result

    async def _annotate_semantic_evidence(
        self,
        request: CollectionRequest,
        result: SourceResult,
    ) -> SourceResult:
        """Hook for source-backed semantic classifiers in the complete web adapter."""
        del request
        return result

    def _should_semantically_escalate(
        self,
        task: SourceTask,
        fetched,
        result: SourceResult,
    ) -> bool:
        if self.agent is None or task.metadata.get("semantic_agent_retry_attempted"):
            return False
        if task.metadata.get("semantic_agent_retry_suppressed"):
            return False
        if fetched.blocked or result.blocked:
            return False
        if classify_public_map_url(str(fetched.final_url)) is None:
            return False
        goals = self._semantic_goals(task)
        if not goals:
            return False
        return self._semantic_goal_fact_count(result, goals) == 0

    def _semantic_goals(self, task: SourceTask) -> list[str]:
        return sorted(
            {
                goal
                for goal in self._research_goals(task)
                if goal in self.semantic_escalation_goals
            }
        )

    def _semantic_goal_fact_count(
        self,
        result: SourceResult,
        goals: list[str],
    ) -> int:
        return sum(
            1
            for observation in result.observations
            if any(self.intent_coverage.supports(observation, goal) for goal in goals)
        )

    @staticmethod
    def _review_fact_count(result: SourceResult) -> int:
        """Backward-compatible structured review count for callers/tests."""
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
            "goals": list(task.metadata.get("semantic_agent_retry_goals") or []),
            "suppressed": task.metadata.get("semantic_agent_retry_suppressed"),
            "agent_output_is_evidence": False,
        }
        review_view = {
            "attempted": bool(task.metadata.get("public_map_review_view_attempted")),
            "accepted": bool(task.metadata.get("public_map_review_view_accepted")),
            "basis": task.metadata.get("public_map_review_view_basis"),
            "candidate_url": task.metadata.get("public_map_review_view_url"),
            "final_url": task.metadata.get("public_map_review_view_final_url"),
            "blocked": bool(task.metadata.get("public_map_review_view_blocked")),
        }
        for observation in result.observations:
            provenance = classify_public_map_url(observation.url)
            if provenance is None:
                continue
            observation.provenance["public_map_source"] = dict(provenance)
            observation.provenance["public_map_semantic_escalation"] = dict(escalation)
            observation.provenance["public_map_review_view"] = dict(review_view)
            observation.quality["public_map_source_identified"] = True

        for evidence in result.evidence:
            provenance = classify_public_map_url(evidence.source.url)
            if provenance is None:
                continue
            evidence.metadata["public_map_source"] = dict(provenance)
            evidence.metadata["public_map_semantic_escalation"] = dict(escalation)
            evidence.metadata["public_map_review_view"] = dict(review_view)

    async def health(self) -> dict[str, object]:
        payload = dict(await super().health())
        payload["public_map_web_provenance"] = {
            "enabled": True,
            "providers": ["yandex_maps_web", "2gis_web", "google_maps_web"],
            "classification_basis": "url_host_path",
            "content_inference": False,
            "paid_api": False,
            "deterministic_review_views": {
                "providers": ["yandex_maps_web", "2gis_web"],
                "browser_verified": True,
                "before_agent": True,
            },
            "semantic_goal_escalation": {
                "version": self.semantic_escalation_version,
                "goals": sorted(self.semantic_escalation_goals),
                "requires_agent_backend": True,
                "agent_output_is_evidence": False,
                "verified_browser_replay": True,
                "source_backed_goal_evidence": True,
            },
        }
        return payload
