from __future__ import annotations

from argus.contracts.models import Observation
from argus.research.followup import HeuristicFollowupResearchPlanner
from argus.research.intent_coverage import IntentCoverageEvaluator

__all__ = [
    "EvidenceAwareHeuristicFollowupResearchPlanner",
    "IntentCoverageEvaluator",
]


class EvidenceAwareHeuristicFollowupResearchPlanner(HeuristicFollowupResearchPlanner):
    """Heuristic follow-up planning that counts only achieved factual coverage."""

    def __init__(self, *args, coverage: IntentCoverageEvaluator | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.coverage = coverage or IntentCoverageEvaluator()

    def _intent_counts(self, observations: list[Observation]) -> dict[str, int]:
        return self.coverage.counts(observations)
