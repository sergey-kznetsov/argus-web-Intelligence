from __future__ import annotations

from argus.contracts.models import Observation
from argus.orchestrator.area_atomic import AreaAwareAtomicCollectionOrchestrator
from argus.research.followup import FollowupResearchPlanner
from argus.research.historical_sources import HistoricalSourceResearchPlanner
from argus.research.public_map_sources import PublicMapSourceResearchPlanner
from argus.research.supervisor import ResearchSupervisor
from argus.sources.base import SourceTask


class AdaptiveResearchAtomicCollectionOrchestrator(AreaAwareAtomicCollectionOrchestrator):
    """Iteratively expand research until requested coverage or collection budgets stop it."""

    def __init__(
        self,
        *args,
        followup_planner: FollowupResearchPlanner | None = None,
        historical_source_planner: HistoricalSourceResearchPlanner | None = None,
        public_map_source_planner: PublicMapSourceResearchPlanner | None = None,
        research_supervisor: ResearchSupervisor | None = None,
        max_followup_rounds: int = 3,
        max_curated_historical_rounds: int = 3,
        max_curated_public_map_rounds: int = 3,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.followup_planner = followup_planner
        self.historical_source_planner = (
            historical_source_planner or HistoricalSourceResearchPlanner()
        )
        self.public_map_source_planner = public_map_source_planner or PublicMapSourceResearchPlanner()
        self.research_supervisor = research_supervisor
        self.max_followup_rounds = max(0, int(max_followup_rounds))
        self.max_curated_historical_rounds = max(0, int(max_curated_historical_rounds))
        self.max_curated_public_map_rounds = max(0, int(max_curated_public_map_rounds))

    async def _expand_historical(
        self,
        record,
        task: SourceTask,
        observations: list[Observation],
        pending: list[SourceTask],
        visited: set[str],
        seen_queries: set[str],
    ) -> None:
        await super()._expand_historical(
            record,
            task,
            observations,
            pending,
            visited,
            seen_queries,
        )
        await self._expand_curated_historical_sources(
            record,
            task,
            observations,
            pending,
            visited,
        )
        await self._expand_curated_public_map_sources(
            record,
            task,
            observations,
            pending,
            visited,
        )
        await self._expand_research_gaps(record, task, observations, pending, visited)

    async def _expand_curated_historical_sources(
        self,
        record,
        task: SourceTask,
        observations: list[Observation],
        pending: list[SourceTask],
        visited: set[str],
    ) -> None:
        if (
            self.discovery is None
            or "historical_context" not in record.request.intents
            or self.max_curated_historical_rounds <= 0
        ):
            return
        round_count = int(record.checkpoint.get("curated_historical_rounds", 0) or 0)
        if round_count >= self.max_curated_historical_rounds:
            return
        remaining_page_budget = max(
            0,
            int(record.request.constraints.max_pages) - len(visited) - len(pending) - 1,
        )
        if remaining_page_budget <= 0:
            return

        committed = await self.repository.list_observations(record.collection_id)
        all_observations = [*committed, *observations]
        seen = {
            str(query)
            for bucket in (
                record.checkpoint.get("queries", []),
                record.checkpoint.get("discovery_queries", []),
                record.checkpoint.get("historical_branch_queries", []),
                record.checkpoint.get("curated_historical_queries", []),
            )
            if isinstance(bucket, list)
            for query in bucket
            if isinstance(query, str) and query.strip()
        }
        query_limit = min(4, remaining_page_budget)
        queries = self.historical_source_planner.queries(
            record.request,
            observations=all_observations,
            seen_queries=seen,
            limit=query_limit,
        )
        if not queries:
            record.checkpoint = {
                **record.checkpoint,
                "curated_historical_complete": True,
                "curated_historical_source_version": self.historical_source_planner.version,
            }
            return

        seen.update(queries)
        constraints = record.request.constraints.model_copy(
            update={"max_pages": remaining_page_budget}
        )
        branch_request = record.request.model_copy(
            update={
                "intents": ["historical_context"],
                "constraints": constraints,
            }
        )
        outcome = await self.discovery.discover(queries, branch_request)
        additions: list[SourceTask] = []
        for branch_task in outcome.tasks[:remaining_page_budget]:
            if branch_task.dedupe_key in visited:
                continue
            branch_task.metadata["curated_historical_round"] = round_count + 1
            branch_task.metadata["curated_historical_from"] = task.url
            branch_task.metadata["curated_historical_queries"] = list(queries)
            additions.append(branch_task)
        self._merge_tasks(pending, additions, record.collection_id)
        record.checkpoint = {
            **record.checkpoint,
            "curated_historical_rounds": round_count + 1,
            "curated_historical_queries": sorted(seen),
            "curated_historical_last_candidates": len(additions),
            "curated_historical_source_version": self.historical_source_planner.version,
        }

    async def _expand_curated_public_map_sources(
        self,
        record,
        task: SourceTask,
        observations: list[Observation],
        pending: list[SourceTask],
        visited: set[str],
    ) -> None:
        requested_intents = [
            intent
            for intent in record.request.intents
            if intent in self.public_map_source_planner.supported_intents
        ]
        if (
            self.discovery is None
            or not requested_intents
            or self.max_curated_public_map_rounds <= 0
            or record.checkpoint.get("curated_public_map_complete") is True
        ):
            return
        round_count = int(record.checkpoint.get("curated_public_map_rounds", 0) or 0)
        if round_count >= self.max_curated_public_map_rounds:
            return
        remaining_page_budget = max(
            0,
            int(record.request.constraints.max_pages) - len(visited) - len(pending) - 1,
        )
        if remaining_page_budget <= 0:
            return

        committed = await self.repository.list_observations(record.collection_id)
        all_observations = [*committed, *observations]
        coverage_counts = self.public_map_source_planner.coverage_counts(
            record.request,
            all_observations,
        )
        remaining_intents = self.public_map_source_planner.remaining_intents(
            record.request,
            all_observations,
        )
        coverage_checkpoint = {
            "curated_public_map_coverage": coverage_counts,
            "curated_public_map_gap_intents": remaining_intents,
            "curated_public_map_target_sources_per_intent": (
                self.public_map_source_planner.target_sources_per_intent
            ),
            "curated_public_map_coverage_version": self.public_map_source_planner.coverage.version,
            "curated_public_map_source_version": self.public_map_source_planner.version,
        }
        if not remaining_intents:
            record.checkpoint = {
                **record.checkpoint,
                **coverage_checkpoint,
                "curated_public_map_complete": True,
                "curated_public_map_exhausted_for_current_anchors": False,
            }
            return

        seen = {
            str(query)
            for bucket in (
                record.checkpoint.get("queries", []),
                record.checkpoint.get("discovery_queries", []),
                record.checkpoint.get("area_entity_queries", []),
                record.checkpoint.get("adaptive_followup_queries", []),
                record.checkpoint.get("curated_public_map_queries", []),
            )
            if isinstance(bucket, list)
            for query in bucket
            if isinstance(query, str) and query.strip()
        }
        query_limit = min(3, remaining_page_budget)
        queries = self.public_map_source_planner.queries(
            record.request,
            observations=all_observations,
            seen_queries=seen,
            limit=query_limit,
        )
        if not queries:
            record.checkpoint = {
                **record.checkpoint,
                **coverage_checkpoint,
                "curated_public_map_complete": False,
                "curated_public_map_exhausted_for_current_anchors": True,
                "curated_public_map_last_candidates": 0,
            }
            return

        seen.update(queries)
        constraints = record.request.constraints.model_copy(
            update={"max_pages": remaining_page_budget}
        )
        branch_request = record.request.model_copy(
            update={
                "intents": remaining_intents,
                "constraints": constraints,
            }
        )
        outcome = await self.discovery.discover(queries, branch_request)
        for error in outcome.errors:
            if error.code != "DISCOVERY_NO_RESULTS":
                record.errors.append(error)

        additions: list[SourceTask] = []
        for branch_task in outcome.tasks[:remaining_page_budget]:
            if branch_task.dedupe_key in visited:
                continue
            branch_task.metadata["curated_public_map_round"] = round_count + 1
            branch_task.metadata["curated_public_map_from"] = task.url
            branch_task.metadata["curated_public_map_queries"] = list(queries)
            branch_task.metadata["curated_public_map_gap_intents"] = list(remaining_intents)
            additions.append(branch_task)
        self._merge_tasks(pending, additions, record.collection_id)
        record.checkpoint = {
            **record.checkpoint,
            **coverage_checkpoint,
            "curated_public_map_complete": False,
            "curated_public_map_exhausted_for_current_anchors": False,
            "curated_public_map_rounds": round_count + 1,
            "curated_public_map_queries": sorted(seen),
            "curated_public_map_last_candidates": len(additions),
        }

    async def _expand_research_gaps(
        self,
        record,
        task: SourceTask,
        observations: list[Observation],
        pending: list[SourceTask],
        visited: set[str],
    ) -> None:
        if self.discovery is None or self.followup_planner is None:
            return
        if self.max_followup_rounds <= 0:
            return
        if len(pending) > 2:
            return

        round_count = int(record.checkpoint.get("adaptive_followup_rounds", 0) or 0)
        if round_count >= self.max_followup_rounds:
            return
        remaining_page_budget = max(
            0,
            int(record.request.constraints.max_pages) - len(visited) - len(pending) - 1,
        )
        if remaining_page_budget <= 0:
            return

        committed = await self.repository.list_observations(record.collection_id)
        all_observations = [*committed, *observations]
        seen = set(record.checkpoint.get("adaptive_followup_queries", []))
        max_queries = min(
            max(1, int(getattr(self.discovery, "max_queries", 8))),
            remaining_page_budget,
        )

        supervisor_decision = None
        followup_request = record.request
        supervisor_queries: list[str] = []
        if self.research_supervisor is not None:
            supervisor_decision = await self.research_supervisor.assess(
                record.request,
                all_observations,
                errors=record.errors,
                seen_queries=seen,
                pending_count=len(pending),
                remaining_page_budget=remaining_page_budget,
            )
            record.checkpoint = {
                **record.checkpoint,
                "research_supervisor": supervisor_decision.as_dict(),
            }
            if not supervisor_decision.continue_research:
                record.checkpoint = {
                    **record.checkpoint,
                    "adaptive_followup_complete": True,
                    "adaptive_followup_notes": ["research_supervisor:no_factual_gap"],
                }
                return
            if supervisor_decision.priority_intents:
                followup_request = record.request.model_copy(
                    update={"intents": list(supervisor_decision.priority_intents)}
                )
            supervisor_queries = list(supervisor_decision.query_hints)

        plan = await self.followup_planner.plan_followups(
            followup_request,
            all_observations,
            seen_queries=seen,
            max_queries=max_queries,
        )
        queries = self._merge_followup_queries(
            supervisor_queries,
            plan.queries,
            seen_queries=seen,
            limit=max_queries,
        )
        notes = list(plan.notes)
        if supervisor_decision is not None:
            notes.append(
                "research_supervisor:"
                f"{supervisor_decision.version}:"
                f"model_assisted={str(supervisor_decision.model_assisted).lower()}"
            )
        if not queries:
            record.checkpoint = {
                **record.checkpoint,
                "adaptive_followup_complete": True,
                "adaptive_followup_notes": notes,
            }
            return

        seen.update(queries)
        constraints = followup_request.constraints.model_copy(
            update={"max_pages": remaining_page_budget}
        )
        branch_request = followup_request.model_copy(update={"constraints": constraints})
        outcome = await self.discovery.discover(queries, branch_request)
        additions: list[SourceTask] = []
        for branch_task in outcome.tasks[:remaining_page_budget]:
            if branch_task.dedupe_key in visited:
                continue
            branch_task.metadata["adaptive_followup_round"] = round_count + 1
            branch_task.metadata["adaptive_followup_from"] = task.url
            branch_task.metadata["adaptive_followup_queries"] = list(queries)
            if supervisor_decision is not None:
                branch_task.metadata["research_supervisor_version"] = supervisor_decision.version
                branch_task.metadata["research_supervisor_model_assisted"] = (
                    supervisor_decision.model_assisted
                )
                branch_task.metadata["research_supervisor_is_evidence"] = False
            additions.append(branch_task)
        self._merge_tasks(pending, additions, record.collection_id)
        record.checkpoint = {
            **record.checkpoint,
            "adaptive_followup_rounds": round_count + 1,
            "adaptive_followup_queries": sorted(seen),
            "adaptive_followup_notes": notes,
            "adaptive_followup_last_candidates": len(additions),
        }

    @staticmethod
    def _merge_followup_queries(
        supervisor_queries: list[str],
        planner_queries: list[str],
        *,
        seen_queries: set[str],
        limit: int,
    ) -> list[str]:
        seen = {str(item).strip().casefold() for item in seen_queries if str(item).strip()}
        result: list[str] = []
        for raw in [*supervisor_queries, *planner_queries]:
            value = " ".join(str(raw).split()).strip()[:512].rstrip()
            key = value.casefold()
            if not value or key in seen:
                continue
            seen.add(key)
            result.append(value)
            if len(result) >= max(0, int(limit)):
                break
        return result
