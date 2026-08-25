from __future__ import annotations

from argus.contracts.models import Observation
from argus.orchestrator.area_atomic import AreaAwareAtomicCollectionOrchestrator
from argus.research.followup import FollowupResearchPlanner
from argus.research.historical_sources import HistoricalSourceResearchPlanner
from argus.research.public_map_sources import PublicMapSourceResearchPlanner
from argus.sources.base import SourceTask


class AdaptiveResearchAtomicCollectionOrchestrator(AreaAwareAtomicCollectionOrchestrator):
    """Iteratively expand research until requested coverage or collection budgets stop it."""

    def __init__(
        self,
        *args,
        followup_planner: FollowupResearchPlanner | None = None,
        historical_source_planner: HistoricalSourceResearchPlanner | None = None,
        public_map_source_planner: PublicMapSourceResearchPlanner | None = None,
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
                "curated_public_map_complete": True,
                "curated_public_map_source_version": self.public_map_source_planner.version,
            }
            return

        seen.update(queries)
        constraints = record.request.constraints.model_copy(
            update={"max_pages": remaining_page_budget}
        )
        branch_request = record.request.model_copy(
            update={
                "intents": requested_intents,
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
            additions.append(branch_task)
        self._merge_tasks(pending, additions, record.collection_id)
        record.checkpoint = {
            **record.checkpoint,
            "curated_public_map_rounds": round_count + 1,
            "curated_public_map_queries": sorted(seen),
            "curated_public_map_last_candidates": len(additions),
            "curated_public_map_source_version": self.public_map_source_planner.version,
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
        plan = await self.followup_planner.plan_followups(
            record.request,
            all_observations,
            seen_queries=seen,
            max_queries=max_queries,
        )
        queries = [query for query in plan.queries if query.strip()]
        if not queries:
            record.checkpoint = {
                **record.checkpoint,
                "adaptive_followup_complete": True,
                "adaptive_followup_notes": plan.notes,
            }
            return

        seen.update(queries)
        constraints = record.request.constraints.model_copy(
            update={"max_pages": remaining_page_budget}
        )
        branch_request = record.request.model_copy(update={"constraints": constraints})
        outcome = await self.discovery.discover(queries, branch_request)
        additions: list[SourceTask] = []
        for branch_task in outcome.tasks[:remaining_page_budget]:
            if branch_task.dedupe_key in visited:
                continue
            branch_task.metadata["adaptive_followup_round"] = round_count + 1
            branch_task.metadata["adaptive_followup_from"] = task.url
            branch_task.metadata["adaptive_followup_queries"] = list(queries)
            additions.append(branch_task)
        self._merge_tasks(pending, additions, record.collection_id)
        record.checkpoint = {
            **record.checkpoint,
            "adaptive_followup_rounds": round_count + 1,
            "adaptive_followup_queries": sorted(seen),
            "adaptive_followup_notes": plan.notes,
            "adaptive_followup_last_candidates": len(additions),
        }
