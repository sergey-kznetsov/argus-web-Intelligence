from __future__ import annotations

from argus.contracts.models import CollectionRequest
from argus.research.intent_evidence import OllamaIntentEvidenceClassifier
from argus.sources.base import SourceResult, SourceTask
from argus.sources.public_map_web import PublicMapProvenanceWebAdapter


class IntentEvidenceWebAdapter(PublicMapProvenanceWebAdapter):
    """Add exact-excerpt semantic intent evidence to the complete generic web stack."""

    def __init__(
        self,
        *args,
        intent_evidence_classifier: OllamaIntentEvidenceClassifier | None = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.intent_evidence_classifier = intent_evidence_classifier

    async def extract(
        self,
        task: SourceTask,
        fetched,
        request: CollectionRequest,
    ) -> SourceResult:
        result = await super().extract(task, fetched, request)
        await self._finalize_recipe_goal_verification(task, request, result)
        return result

    async def _annotate_semantic_evidence(
        self,
        request: CollectionRequest,
        result: SourceResult,
    ) -> SourceResult:
        if self.intent_evidence_classifier is None:
            return result
        return await self.intent_evidence_classifier.annotate(request, result)

    async def health(self) -> dict[str, object]:
        payload = dict(await super().health())
        if self.intent_evidence_classifier is not None:
            payload["intent_evidence_classifier"] = {
                "version": self.intent_evidence_classifier.version,
                "supported_intents": sorted(
                    self.intent_evidence_classifier.supported_intents
                ),
                "exact_source_excerpt_required": True,
                "deterministic_marker_required_for": sorted(
                    self.intent_evidence_classifier.marker_required_intents
                ),
                "semantic_label_model_assisted": True,
                "model_output_is_evidence": False,
            }
        return payload
