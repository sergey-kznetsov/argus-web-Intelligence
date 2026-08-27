from __future__ import annotations

from argus.contracts.models import Observation
from argus.orchestrator.observed_atomic import ObservedAtomicCollectionOrchestrator
from argus.research.entities import AreaEntityResearchPlanner
from argus.sources.base import SourceTask


class AreaAwareAtomicCollectionOrchestrator(ObservedAtomicCollectionOrchestrator):
    """Atomic orchestrator that recursively researches factual entities found in the area."""

    execution_budget_version = "execution-budget/1"

    def __init__(
        self,
        *args,
        area_entity_planner: AreaEntityResearchPlanner | None = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.area_entity_planner = area_entity_planner

    async def _expand_historical(
        self,
        record,
        task: SourceTask,
        observations: list[Observation],
        pending: list[SourceTask],
        visited: set[str],
        seen_queries: set[str],
    ) -> None:
        await self._expand_area_entities(record, task, observations, pending, visited)
        await super()._expand_historical(
            record,
            task,
            observations,
            pending,
            visited,
            seen_queries,
        )

    def _remaining_execution_budget(self, record, visited: set[str]) -> int:
        """Return executable page slots after the current task.

        ``pending`` is intentionally excluded. A queued URL is only a candidate and has not
        consumed the page budget until the orchestrator actually processes it. Counting queue
        length here allowed broad depth-crawl discovery to starve focused research branches.
        """

        return max(
            0,
            int(record.request.constraints.max_pages) - len(visited) - 1,
        )

    async def _expand_area_entities(
        self,
        record,
        task: SourceTask,
        observations: list[Observation],
        pending: list[SourceTask],
        visited: set[str],
    ) -> None:
        if self.discovery is None or self.area_entity_planner is None or not observations:
            return

        raw_depth = task.metadata.get("area_branch_depth", 0)
        try:
            branch_depth = max(0, int(raw_depth))
        except (TypeError, ValueError):
            branch_depth = 0
        if branch_depth >= record.request.constraints.max_depth:
            return

        seen_queries = set(record.checkpoint.get("area_entity_queries", []))
        remaining_page_budget = self._remaining_execution_budget(record, visited)
        if remaining_page_budget <= 0:
            return
        query_limit = min(
            max(1, int(getattr(self.discovery, "max_queries", 8))),
            remaining_page_budget,
        )
        queries = self.area_entity_planner.expand(
            record.request,
            observations,
            seen_queries=seen_queries,
            limit=query_limit,
        )
        if not queries:
            return

        seen_queries.update(queries)
        requested_intents = [
            intent
            for intent in record.request.intents
            if intent in self.area_entity_planner.area_intents
        ]
        if not requested_intents:
            return
        branch_constraints = record.request.constraints.model_copy(
            update={"max_pages": remaining_page_budget}
        )
        branch_request = record.request.model_copy(
            update={
                "intents": requested_intents,
                "constraints": branch_constraints,
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
            branch_task.metadata["area_branch_depth"] = branch_depth + 1
            branch_task.metadata["area_branch_from"] = task.url
            branch_task.metadata["area_entity_queries"] = list(queries)
            additions.append(branch_task)
        self._merge_tasks(pending, additions, record.collection_id)
        record.checkpoint = {
            **record.checkpoint,
            "area_entity_queries": sorted(seen_queries),
            "execution_budget_version": self.execution_budget_version,
        }
