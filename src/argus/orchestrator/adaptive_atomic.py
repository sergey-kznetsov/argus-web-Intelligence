from __future__ import annotations

from collections import deque

from argus.contracts.models import Observation
from argus.orchestrator.area_atomic import AreaAwareAtomicCollectionOrchestrator
from argus.research.entity_hypotheses import OllamaEntityHypothesisExtractor
from argus.research.followup import FollowupResearchPlanner
from argus.research.historical_sources import HistoricalSourceResearchPlanner
from argus.research.public_map_sources import PublicMapSourceResearchPlanner
from argus.research.supervisor import ResearchSupervisor
from argus.sources.base import SourceTask


class AdaptiveResearchAtomicCollectionOrchestrator(AreaAwareAtomicCollectionOrchestrator):
    """Iteratively expand research until requested coverage or collection budgets stop it."""

    research_queue_priority_version = "research-queue-priority/3"

    def __init__(
        self,
        *args,
        followup_planner: FollowupResearchPlanner | None = None,
        historical_source_planner: HistoricalSourceResearchPlanner | None = None,
        public_map_source_planner: PublicMapSourceResearchPlanner | None = None,
        research_supervisor: ResearchSupervisor | None = None,
        entity_hypothesis_extractor: OllamaEntityHypothesisExtractor | None = None,
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
        self.entity_hypothesis_extractor = entity_hypothesis_extractor
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
        self._prioritize_pending(record, pending)

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
        remaining_page_budget = self._remaining_execution_budget(record, visited)
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
                "execution_budget_version": self.execution_budget_version,
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
            "execution_budget_version": self.execution_budget_version,
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
        remaining_page_budget = self._remaining_execution_budget(record, visited)
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
        self._checkpoint_public_map_coverage(
            record,
            coverage_counts=coverage_counts,
            remaining_intents=remaining_intents,
            exhausted=False,
        )
        if not remaining_intents:
            record.checkpoint = {
                **record.checkpoint,
                "curated_public_map_complete": True,
            }
            return

        seen = {
            str(query)
            for bucket in (
                record.checkpoint.get("queries", []),
                record.checkpoint.get("discovery_queries", []),
                record.checkpoint.get("public_map_queries", []),
            )
            if isinstance(bucket, list)
            for query in bucket
            if isinstance(query, str) and query.strip()
        }
        query_limit = min(4, remaining_page_budget)
        queries = self.public_map_source_planner.queries(
            record.request,
            observations=all_observations,
            seen_queries=seen,
            limit=query_limit,
        )
        if not queries:
            # Current anchors cannot produce another useful map query. Keep the research
            # incomplete so a later newly discovered entity/address can reopen this branch.
            self._checkpoint_public_map_coverage(
                record,
                coverage_counts=coverage_counts,
                remaining_intents=remaining_intents,
                exhausted=True,
            )
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
        additions: list[SourceTask] = []
        for branch_task in outcome.tasks[:remaining_page_budget]:
            if branch_task.dedupe_key in visited:
                continue
            branch_task.metadata["curated_public_map_round"] = round_count + 1
            branch_task.metadata["curated_public_map_from"] = task.url
            branch_task.metadata["public_map_queries"] = list(queries)
            additions.append(branch_task)
        self._merge_tasks(pending, additions, record.collection_id)
        record.checkpoint = {
            **record.checkpoint,
            "curated_public_map_rounds": round_count + 1,
            "public_map_queries": sorted(seen),
            "curated_public_map_last_candidates": len(additions),
            "public_map_source_version": self.public_map_source_planner.version,
            "execution_budget_version": self.execution_budget_version,
        }

    def _checkpoint_public_map_coverage(
        self,
        record,
        *,
        coverage_counts: dict[str, int],
        remaining_intents: list[str],
        exhausted: bool,
    ) -> None:
        record.checkpoint = {
            **record.checkpoint,
            "curated_public_map_coverage": coverage_counts,
            "curated_public_map_remaining_intents": list(remaining_intents),
            "curated_public_map_target_source_count": (
                self.public_map_source_planner.target_source_count
            ),
            "curated_public_map_coverage_evaluator_version": (
                self.public_map_source_planner.coverage.version
            ),
            "public_map_source_version": self.public_map_source_planner.version,
            "curated_public_map_exhausted_for_current_anchors": exhausted,
            "execution_budget_version": self.execution_budget_version,
        }

    async def _expand_research_gaps(
        self,
        record,
        task: SourceTask,
        observations: list[Observation],
        pending: list[SourceTask],
        visited: set[str],
    ) -> None:
        if (
            self.discovery is None
            or self.followup_planner is None
            or self.max_followup_rounds <= 0
        ):
            return
        round_count = int(record.checkpoint.get("adaptive_followup_rounds", 0) or 0)
        if round_count >= self.max_followup_rounds:
            return
        remaining_page_budget = self._remaining_execution_budget(record, visited)
        if remaining_page_budget <= 0:
            return
        if len(pending) > 2:
            return

        committed = await self.repository.list_observations(record.collection_id)
        all_observations = [*committed, *observations]
        supervisor_decision = None
        if self.research_supervisor is not None:
            supervisor_decision = await self.research_supervisor.decide(
                record.request,
                all_observations,
                pending_tasks=len(pending),
                remaining_pages=remaining_page_budget,
            )
            record.checkpoint = {
                **record.checkpoint,
                "research_supervisor": supervisor_decision.as_checkpoint(),
            }
            if not supervisor_decision.continue_research:
                record.checkpoint = {
                    **record.checkpoint,
                    "adaptive_followup_complete": True,
                }
                return

        seen = {
            str(query)
            for bucket in (
                record.checkpoint.get("queries", []),
                record.checkpoint.get("discovery_queries", []),
                record.checkpoint.get("adaptive_followup_queries", []),
            )
            if isinstance(bucket, list)
            for query in bucket
            if isinstance(query, str) and query.strip()
        }
        max_queries = min(6, remaining_page_budget)
        entity_queries: list[str] = []
        if self.entity_hypothesis_extractor is not None:
            hypothesis_plan = await self.entity_hypothesis_extractor.propose_queries(
                record.request,
                all_observations,
                seen_queries=seen,
                max_queries=max_queries,
            )
            entity_queries = list(hypothesis_plan.queries)
            record.checkpoint = {
                **record.checkpoint,
                "entity_hypothesis_version": hypothesis_plan.version,
                "entity_hypothesis_queries": list(entity_queries),
                "entity_hypothesis_model_output_is_evidence": False,
            }

        supervisor_queries = (
            list(supervisor_decision.query_hints) if supervisor_decision is not None else []
        )
        uncovered_intents = (
            list(supervisor_decision.priority_intents)
            if supervisor_decision is not None and supervisor_decision.priority_intents
            else list(record.request.intents)
        )
        followup_request = record.request.model_copy(update={"intents": uncovered_intents})
        plan = await self.followup_planner.plan_followups(
            followup_request,
            all_observations,
            seen_queries=seen,
            max_queries=max_queries,
        )
        queries = self._merge_followup_queries(
            [*entity_queries, *supervisor_queries],
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
        if entity_queries:
            notes.append(
                "entity_hypotheses:"
                f"{self.entity_hypothesis_extractor.version}:queries={len(entity_queries)}"
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
            if entity_queries:
                branch_task.metadata["entity_hypothesis_navigation"] = True
                branch_task.metadata["entity_hypothesis_is_evidence"] = False
            additions.append(branch_task)
        self._merge_tasks(pending, additions, record.collection_id)
        record.checkpoint = {
            **record.checkpoint,
            "adaptive_followup_rounds": round_count + 1,
            "adaptive_followup_queries": sorted(seen),
            "adaptive_followup_notes": notes,
            "adaptive_followup_last_candidates": len(additions),
            "execution_budget_version": self.execution_budget_version,
        }

    def _prioritize_pending(self, record, pending: list[SourceTask]) -> None:
        """Prioritize safe tasks without allowing one requested goal to starve the others.

        Branch quality remains the primary ordering dimension: curated factual-gap work outranks
        general follow-up, area expansion and ordinary depth crawl. Within an equivalent branch
        and depth tier, requested goals are interleaved round-robin. This prevents a large link
        fan-out for one intent from consuming the complete page budget while other requested
        intents already have executable factual candidates waiting.

        Queue ordering never promotes a candidate into Evidence and never changes task identity.
        """

        if not pending:
            return

        requested_order: list[str] = []
        seen_requested: set[str] = set()
        for raw in record.request.intents:
            intent = str(raw).strip().casefold()
            if not intent or intent in seen_requested:
                continue
            seen_requested.add(intent)
            requested_order.append(intent)
        requested = set(requested_order)

        base_sorted = sorted(pending, key=lambda item: self._pending_priority(item, requested))
        cursor = self._queue_fairness_cursor(record, len(requested_order))
        pending[:] = self._interleave_priority_tiers(
            base_sorted,
            requested_order=requested_order,
            cursor=cursor,
        )

        selected_goal = self._task_requested_goal(pending[0], requested_order)
        next_cursor = cursor
        if selected_goal is not None and requested_order:
            next_cursor = (requested_order.index(selected_goal) + 1) % len(requested_order)

        record.checkpoint = {
            **record.checkpoint,
            "research_queue_priority_version": self.research_queue_priority_version,
            "research_queue_candidate_count": len(pending),
            "research_queue_fairness_cursor": next_cursor,
            "research_queue_next_goal": selected_goal,
            "research_queue_next": [
                {
                    "source_id": item.source_id,
                    "goal": item.goal,
                    "depth": item.depth,
                    "focused_branch": self._focused_branch(item),
                    "fairness_goal": self._task_requested_goal(item, requested_order),
                }
                for item in pending[:5]
            ],
        }

    @classmethod
    def _interleave_priority_tiers(
        cls,
        base_sorted: list[SourceTask],
        *,
        requested_order: list[str],
        cursor: int,
    ) -> list[SourceTask]:
        if len(requested_order) <= 1 or len(base_sorted) <= 1:
            return list(base_sorted)

        requested = set(requested_order)
        rotated = [
            *requested_order[cursor:],
            *requested_order[:cursor],
        ]
        result: list[SourceTask] = []
        start = 0
        while start < len(base_sorted):
            first_priority = cls._pending_priority(base_sorted[start], requested)
            tier_key = first_priority[:3]
            end = start + 1
            while end < len(base_sorted):
                priority = cls._pending_priority(base_sorted[end], requested)
                if priority[:3] != tier_key:
                    break
                end += 1

            lanes: dict[str, deque[SourceTask]] = {
                goal: deque() for goal in requested_order
            }
            unassigned: deque[SourceTask] = deque()
            for item in base_sorted[start:end]:
                goal = cls._task_requested_goal(item, requested_order)
                if goal is None:
                    unassigned.append(item)
                else:
                    lanes[goal].append(item)

            while any(lanes[goal] for goal in rotated):
                for goal in rotated:
                    lane = lanes[goal]
                    if lane:
                        result.append(lane.popleft())
            result.extend(unassigned)
            start = end

        return result

    @staticmethod
    def _queue_fairness_cursor(record, goal_count: int) -> int:
        if goal_count <= 0:
            return 0
        raw = record.checkpoint.get("research_queue_fairness_cursor", 0)
        try:
            return max(0, int(raw)) % goal_count
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _task_requested_goal(
        task: SourceTask,
        requested_order: list[str],
    ) -> str | None:
        requested = set(requested_order)
        goal = str(task.goal).strip().casefold()
        if goal in requested:
            return goal
        raw_goals = task.metadata.get("research_goals")
        if isinstance(raw_goals, list):
            normalized = {
                str(value).strip().casefold()
                for value in raw_goals
                if str(value).strip()
            }
            for requested_goal in requested_order:
                if requested_goal in normalized:
                    return requested_goal
        return None

    @classmethod
    def _pending_priority(
        cls,
        task: SourceTask,
        requested: set[str],
    ) -> tuple[int, int, int, float, str]:
        branch = cls._focused_branch(task)
        if branch in {"curated_public_map", "curated_historical"}:
            branch_rank = 0
        elif branch == "adaptive_followup":
            branch_rank = 1
        elif branch == "area_entity":
            branch_rank = 2
        elif int(task.depth) <= 0:
            branch_rank = 3
        else:
            branch_rank = 4

        goals = {str(task.goal).strip().casefold()}
        raw_goals = task.metadata.get("research_goals")
        if isinstance(raw_goals, list):
            goals.update(
                str(value).strip().casefold()
                for value in raw_goals
                if str(value).strip()
            )
        goal_rank = 0 if goals.intersection(requested) else 1
        try:
            navigation_score = float(task.metadata.get("discovery_navigation_score", 0.0) or 0.0)
        except (TypeError, ValueError):
            navigation_score = 0.0
        return (
            branch_rank,
            goal_rank,
            max(0, int(task.depth)),
            -navigation_score,
            task.url,
        )

    @staticmethod
    def _focused_branch(task: SourceTask) -> str | None:
        metadata = task.metadata
        if metadata.get("curated_public_map_round"):
            return "curated_public_map"
        if metadata.get("curated_historical_round"):
            return "curated_historical"
        if metadata.get("adaptive_followup_round"):
            return "adaptive_followup"
        if metadata.get("area_branch_depth"):
            return "area_entity"
        return None

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
