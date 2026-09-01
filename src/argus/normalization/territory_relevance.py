from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from argus.contracts.models import CollectionRequest, Evidence, Observation
from argus.research.territory_relevance import TerritoryRelevanceEvaluator


class TerritoryRelevanceProofNormalizer:
    """Persist deterministic territory relevance as inspectable source-backed proof.

    The evaluator uses only fetched source text/data or explicit source coordinates. Search
    queries and planning metadata are not evidence. This normalizer merely records that
    deterministic decision on the Observation and its linked Evidence; it does not assign
    truth confidence and it does not alter the source payload.
    """

    version = "territory-relevance-proof/1"

    def __init__(self, evaluator: TerritoryRelevanceEvaluator | None = None) -> None:
        self.evaluator = evaluator or TerritoryRelevanceEvaluator()

    def normalize(
        self,
        request: CollectionRequest,
        observations: Sequence[Observation],
        evidence: Sequence[Evidence],
    ) -> None:
        evidence_by_observation: defaultdict[str, list[Evidence]] = defaultdict(list)
        for item in evidence:
            if item.observation_id:
                evidence_by_observation[item.observation_id].append(item)

        for observation in observations:
            result = self.evaluator.evaluate(request, observation)
            proof: dict[str, object] = {
                "version": self.version,
                "evaluator_version": self.evaluator.version,
                "matched": result.matched,
                "basis": result.basis,
                "matched_anchors": list(result.matched_anchors),
                "source_backed": True,
                "planning_metadata_used_as_evidence": False,
            }
            if result.distance_meters is not None:
                proof["distance_meters"] = result.distance_meters

            observation.provenance["territory_relevance"] = proof
            observation.quality["territory_relevant"] = result.matched

            for item in evidence_by_observation.get(observation.observation_id, ()): 
                item.metadata["territory_relevance_verified"] = result.matched
                item.metadata["territory_relevance"] = {
                    "version": self.version,
                    "evaluator_version": self.evaluator.version,
                    "matched": result.matched,
                    "basis": result.basis,
                    "matched_anchors": list(result.matched_anchors),
                    "source_backed": True,
                }
                if result.distance_meters is not None:
                    item.metadata["territory_relevance"]["distance_meters"] = (
                        result.distance_meters
                    )


__all__ = ["TerritoryRelevanceProofNormalizer"]
