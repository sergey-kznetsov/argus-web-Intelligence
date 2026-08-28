from __future__ import annotations

from argus.contracts.models import CollectionRequest
from argus.research.historical_relevance import HistoricalTerritoryRelevanceEvaluator
from argus.research.intent_evidence import OllamaIntentEvidenceClassifier
from argus.sources.base import SourceResult, SourceTask
from argus.sources.public_map_web import PublicMapProvenanceWebAdapter


class IntentEvidenceWebAdapter(PublicMapProvenanceWebAdapter):
    """Add exact-excerpt semantic intent evidence to the complete generic web stack."""

    historical_archive_provenance_version = "historical-archive-page/1"
    historical_relevance = HistoricalTerritoryRelevanceEvaluator()

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
        self._attach_historical_archive_provenance(task, request, result)
        await self._finalize_recipe_goal_verification(task, request, result)
        return result

    @classmethod
    def _attach_historical_archive_provenance(
        cls,
        task: SourceTask,
        request: CollectionRequest,
        result: SourceResult,
    ) -> None:
        """Mark a fetched, territorially relevant Wayback page as historical Evidence.

        CDX rows are navigation/index evidence only. Historical factual coverage is granted
        only after Generic Web fetches the archived capture itself and source-backed content
        from that capture independently matches the requested territory.
        """

        original_url = str(task.metadata.get("archive_original_url") or "").strip()
        timestamp = str(task.metadata.get("archive_timestamp") or "").strip()
        if not original_url or not timestamp or not result.observations:
            return

        provider = str(task.metadata.get("discovery_provider") or "wayback_cdx").strip()
        archive = {
            "version": cls.historical_archive_provenance_version,
            "historical_capture": True,
            "provider": provider or "wayback_cdx",
            "original_url": original_url,
            "capture_timestamp": timestamp,
        }
        annotated_ids: set[str] = set()
        for observation in result.observations:
            relevance = cls.historical_relevance.evaluate(request, observation)
            if not relevance.matched:
                continue
            observation.provenance["archive"] = {
                **archive,
                "territory_relevance_basis": relevance.basis,
                "territory_matched_anchors": list(relevance.matched_anchors),
            }
            observation.quality["historical_capture"] = True
            observation.quality["historical_territory_relevant"] = True
            observation.data.setdefault("archive_original_url", original_url)
            observation.data.setdefault("archive_timestamp", timestamp)
            if observation.source_kind == "web_page":
                observation.source_kind = "historical_page_version"
            annotated_ids.add(observation.observation_id)

        if not annotated_ids:
            return
        for evidence in result.evidence:
            if evidence.observation_id not in annotated_ids:
                continue
            evidence.metadata["archive"] = dict(archive)

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
        payload["historical_archive_page_provenance"] = {
            "version": self.historical_archive_provenance_version,
            "requires_fetched_capture": True,
            "requires_source_backed_territory_match": True,
            "capture_index_is_factual_context": False,
        }
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
