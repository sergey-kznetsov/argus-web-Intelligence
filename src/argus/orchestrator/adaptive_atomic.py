from __future__ import annotations

from argus.contracts.models import Observation
from argus.orchestrator.area_atomic import AreaAwareAtomicCollectionOrchestrator
from argus.research.followup import FollowupResearchPlanner
from argus.sources.base import SourceTask


class AdaptiveResearchAtomicCollectionOrchestrator(AreaAwareAtomicCollectionOrchestrator):
    """Iteratively expand research until requested coverage or collection budgets stop it."""

    def __init__(
        self,
        *args,
        followup_planner: FollowupResearchPlanner | None = None,
        max_followup_rounds: int = 3,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.followup_planner = followup_planner
        self.max_followup_rounds = max(0, int(max_followup_rounds))

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
        await self._expand_research_gaps(record, task, observations, pending, visited)

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
            int(record.request.constraints.max_pages) - len(visited) - len(pending),
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
        outcome = await self.discovery.discover(queries, record.request)
        additions: list[SourceTask] = []
        for branch_task in outcome.tasks:
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
