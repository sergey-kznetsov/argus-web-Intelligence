from __future__ import annotations

from argus.contracts.models import CollectionRequest
from argus.crawler.models import FetchResult
from argus.normalization.public_map_provenance import (
    classify_public_map_url,
    preferred_public_map_review_url,
)
from argus.research.coverage import IntentCoverageEvaluator
from argus.security.urls import UnsafeUrlError
from argus.sources.base import SourceResult, SourceTask
from argus.sources.historical_web import HistoricalTimelineWebAdapter


class PublicMapProvenanceWebAdapter(HistoricalTimelineWebAdapter):
    """Attach public-map provenance and resolve factual goals without paid APIs.

    Public map pages are frequently interactive applications. A technically successful
    FAST/BROWSER response is not enough when a requested factual goal has not actually
    been evidenced. ARGUS therefore evaluates source-backed, request-aware intent coverage,
    prefers a deterministic public review view for compatible social goals, and may then
    use bounded AGENT -> verified browser replay for unresolved public-map facts. Agent
    output is navigation only and is never Evidence.
    """

    semantic_escalation_version = "public-map-goal-escalation/6"
    # Kept as the deterministic/social priority set for compatibility and diagnostics.
    # Custom or future factual goals are no longer required to be enumerated here before
    # bounded public-map AGENT navigation can attempt to reveal them.
    semantic_escalation_goals = frozenset(
        {"reviews", "comments", "discussions", "complaints"}
    )
    deterministic_review_view_goals = semantic_escalation_goals
    generic_web_only_goals = frozenset({"incidents"})
    max_semantic_goals = 8
    max_semantic_goal_chars = 128
    max_semantic_agent_rounds = 2
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
        agent_context_fetch = fetched

        if goals and self._semantic_goal_fact_count(result, goals, request=request) == 0:
            result, agent_context_fetch = await self._try_deterministic_review_view(
                task,
                fetched,
                request,
                result,
                goals,
            )

        if self._should_semantically_escalate(task, fetched, result, request=request):
            result = await self._semantic_agent_rounds(
                task,
                request,
                result,
                goals,
                agent_context_fetch,
            )

        self._attach_public_map_provenance(result, task)
        return result

    async def _semantic_agent_rounds(
        self,
        task: SourceTask,
        request: CollectionRequest,
        result: SourceResult,
        goals: list[str],
        context_fetch: FetchResult,
    ) -> SourceResult:
        task.metadata["semantic_agent_retry_attempted"] = True
        task.metadata["semantic_agent_retry_goals"] = goals
        task.metadata["semantic_agent_retry_reason"] = (
            "review_goal_without_review_fact"
            if goals == ["reviews"]
            else "semantic_goal_without_evidence"
        )
        before_score = self._semantic_goal_fact_count(result, goals, request=request)
        current_context = context_fetch
        accepted = False
        rounds_completed = 0

        for round_index in range(self.max_semantic_agent_rounds):
            agent_task = self._agent_task_for_context(task, current_context)
            guided = await self._agent_guided_fetch(
                agent_task,
                context_fetch=current_context,
            )
            rounds_completed = round_index + 1
            if guided is None:
                break
            if guided.blocked:
                task.metadata["semantic_agent_retry_suppressed"] = "agent_verification_blocked"
                break

            guided_result = await super().extract(task, guided, request)
            guided_result = await self._annotate_semantic_evidence(request, guided_result)
            after_score = self._semantic_goal_fact_count(
                guided_result,
                goals,
                request=request,
            )
            if after_score > before_score:
                result = guided_result
                accepted = True
                break

            # A verified first step can reveal new controls that were absent from the
            # prior DOM. Feed that verified DOM into one more bounded planning round.
            # RecipeWeb will extend only the exact active recipe that produced it.
            if round_index + 1 < self.max_semantic_agent_rounds:
                if not (200 <= int(guided.status_code) < 400 and guided.text.strip()):
                    break
                current_context = guided

        task.metadata["semantic_agent_retry_rounds"] = rounds_completed
        task.metadata["semantic_agent_retry_accepted"] = accepted
        return result

    async def _try_deterministic_review_view(
        self,
        task: SourceTask,
        fetched: FetchResult,
        request: CollectionRequest,
        result: SourceResult,
        goals: list[str],
    ) -> tuple[SourceResult, FetchResult]:
        if fetched.blocked or result.blocked:
            return result, fetched
        if not set(goals).intersection(self.deterministic_review_view_goals):
            return result, fetched
        candidate_url = preferred_public_map_review_url(str(fetched.final_url))
        if candidate_url is None:
            return result, fetched

        task.metadata["public_map_review_view_attempted"] = True
        task.metadata["public_map_review_view_url"] = candidate_url
        task.metadata["public_map_review_view_basis"] = "provider_public_url_shape"
        before_score = self._semantic_goal_fact_count(result, goals, request=request)
        try:
            candidate_fetched = await self.browser.fetch(candidate_url)
        except UnsafeUrlError:
            raise
        except Exception as exc:
            task.metadata["public_map_review_view_accepted"] = False
            task.metadata["public_map_review_view_error_type"] = type(exc).__name__
            return result, fetched

        task.metadata["public_map_review_view_status_code"] = candidate_fetched.status_code
        task.metadata["public_map_review_view_final_url"] = candidate_fetched.final_url
        if candidate_fetched.blocked:
            task.metadata["public_map_review_view_accepted"] = False
            task.metadata["public_map_review_view_blocked"] = True
            task.metadata["semantic_agent_retry_suppressed"] = "review_view_blocked"
            return result, fetched

        candidate_result = await super().extract(task, candidate_fetched, request)
        candidate_result = await self._annotate_semantic_evidence(request, candidate_result)
        after_score = self._semantic_goal_fact_count(
            candidate_result,
            goals,
            request=request,
        )
        accepted = after_score > before_score
        task.metadata["public_map_review_view_accepted"] = accepted
        if accepted:
            return candidate_result, candidate_fetched

        # If the deterministic view itself loaded successfully, any later AGENT plan
        # must be compiled/replayed against that exact DOM URL. Otherwise selectors
        # chosen from /reviews could be incorrectly persisted against the original card.
        if 200 <= int(candidate_fetched.status_code) < 400 and candidate_fetched.text.strip():
            return result, candidate_fetched
        return result, fetched

    @staticmethod
    def _agent_task_for_context(task: SourceTask, context_fetch: FetchResult) -> SourceTask:
        context_url = str(context_fetch.final_url or task.url)
        if context_url == task.url:
            return task
        return SourceTask(
            source_id=task.source_id,
            goal=task.goal,
            url=context_url,
            depth=task.depth,
            metadata=task.metadata,
            task_key=task.task_key,
        )

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
        *,
        request: CollectionRequest,
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
        return self._semantic_goal_fact_count(result, goals, request=request) == 0

    def _semantic_goals(self, task: SourceTask) -> list[str]:
        """Return bounded factual map goals without consumer-specific whitelists.

        Research goals originate from the request/planner and are navigation metadata, not
        Evidence. Public-map AGENT may attempt to reveal any such factual goal except intents
        explicitly reserved for generic-web research. The downstream exact-excerpt classifier
        still decides whether the verified page actually proves a goal.
        """

        result: list[str] = []
        seen: set[str] = set()
        for raw in self._research_goals(task):
            goal = " ".join(str(raw).split()).strip().casefold()
            if (
                not goal
                or len(goal) > self.max_semantic_goal_chars
                or goal in self.generic_web_only_goals
                or goal in seen
            ):
                continue
            seen.add(goal)
            result.append(goal)
            if len(result) >= self.max_semantic_goals:
                break
        return sorted(result)

    def _semantic_goal_fact_count(
        self,
        result: SourceResult,
        goals: list[str],
        *,
        request: CollectionRequest,
    ) -> int:
        return sum(
            1
            for observation in result.observations
            if any(
                self.intent_coverage.supports(observation, goal, request=request)
                for goal in goals
            )
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
            "rounds": int(task.metadata.get("semantic_agent_retry_rounds", 0) or 0),
            "max_rounds": self.max_semantic_agent_rounds,
            "suppressed": task.metadata.get("semantic_agent_retry_suppressed"),
            "agent_output_is_evidence": False,
        }
        review_view = {
            "attempted": bool(task.metadata.get("public_map_review_view_attempted")),
            "accepted": bool(task.metadata.get("public_map_review_view_accepted")),
            "basis": task.metadata.get("public_map_review_view_basis"),
            "candidate_url": task.metadata.get("public_map_review_view_url"),
            "final_url": task.metadata.get("public_map_review_view_final_url"),
            "status_code": task.metadata.get("public_map_review_view_status_code"),
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
                "agent_recipe_bound_to_analyzed_view": True,
                "goals": sorted(self.deterministic_review_view_goals),
            },
            "semantic_goal_escalation": {
                "version": self.semantic_escalation_version,
                "goal_policy": "bounded_task_research_goals",
                "priority_social_goals": sorted(self.semantic_escalation_goals),
                "generic_web_only_goals": sorted(self.generic_web_only_goals),
                "max_goals": self.max_semantic_goals,
                "requires_agent_backend": True,
                "agent_output_is_evidence": False,
                "verified_browser_replay": True,
                "source_backed_goal_evidence": True,
                "request_aware_coverage": True,
                "max_rounds": self.max_semantic_agent_rounds,
                "verified_recipe_extension": True,
            },
        }
        return payload
