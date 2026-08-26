from __future__ import annotations

from collections.abc import Iterable

from argus.contracts.models import CollectionRequest
from argus.research.discovery import DiscoveryOutcome, DiscoveryService
from argus.research.input_candidates import research_input_candidates
from argus.research.planner import ResearchPlan, ResearchPlanner
from argus.sources.base import SourceTask


def _merge_research_inputs(task: SourceTask, values: Iterable[object]) -> None:
    existing = task.metadata.get("research_input_candidates", [])
    raw_existing = existing if isinstance(existing, list) else []
    result: list[str] = []
    seen: set[str] = set()
    for raw in [*raw_existing, *values]:
        value = " ".join(str(raw).split()).strip()[:512].rstrip()
        key = value.casefold()
        if not value or key in seen:
            continue
        seen.add(key)
        result.append(value)
        if len(result) >= 8:
            break
    task.metadata["research_input_candidates"] = result
    task.metadata["research_input_candidates_navigation_only"] = True
    task.metadata["research_input_candidates_are_evidence"] = False


class ResearchInputPlanner:
    """Decorate any planner so its tasks carry safe public-form input candidates."""

    def __init__(self, delegate: ResearchPlanner) -> None:
        self.delegate = delegate

    async def plan(self, request: CollectionRequest) -> ResearchPlan:
        plan = await self.delegate.plan(request)
        values = research_input_candidates(request, extra_values=plan.queries)
        for task in plan.tasks:
            _merge_research_inputs(task, values)
        return plan


class ResearchInputDiscoveryService(DiscoveryService):
    """Attach caller/ARGUS navigation values to every discovered public URL task."""

    async def discover(
        self,
        queries: list[str],
        request: CollectionRequest,
    ) -> DiscoveryOutcome:
        outcome = await super().discover(queries, request)
        values = research_input_candidates(request, extra_values=queries)
        for task in outcome.tasks:
            _merge_research_inputs(task, values)
        return outcome
