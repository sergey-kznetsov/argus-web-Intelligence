from __future__ import annotations

from argus.research.followup import HeuristicFollowupResearchPlanner
from argus.research.intent_coverage import IntentCoverageEvaluator

__all__ = [
    "EvidenceAwareHeuristicFollowupResearchPlanner",
    "IntentCoverageEvaluator",
]


class EvidenceAwareHeuristicFollowupResearchPlanner(HeuristicFollowupResearchPlanner):
    """Backward-compatible explicit name for the evidence-aware heuristic planner."""

    def __init__(self, *args, coverage: IntentCoverageEvaluator | None = None, **kwargs) -> None:
        super().__init__(*args, coverage=coverage, **kwargs)
