from __future__ import annotations

from typing import Protocol

from argus.contracts.models import CollectionRequest
from argus.research.historical_relevance import HistoricalTerritoryRelevanceEvaluator
from argus.sources.base import SourceResult, SourceTask
from argus.sources.public_map_web import PublicMapProvenanceWebAdapter
from argus.sources.web_content import extract_readable_text


class IntentEvidenceAnnotator(Protocol):
    version: str
    builtin_intents: frozenset[str]
    marker_required_intents: frozenset[str]

    async def annotate(
        self,
        request: CollectionRequest,
        result: SourceResult,
    ) -> SourceResult: ...


class IntentEvidenceWebAdapter(PublicMapProvenanceWebAdapter):
    """Generic web acquisition with optional source-backed intent annotations.

    The annotator is a control-layer extension: it may label only exact excerpts that are
    already present in fetched source text. Its own output is never factual Evidence.
    """

    historical_archive_provenance_version = "historical-archive-page/1"
    source_contour_provenance_version = "source-contour-provenance/3"
    historical_relevance = HistoricalTerritoryRelevanceEvaluator()
    _source_contour_child_keys = (
        "source_contour",
        "source_contour_version",
        "source_contour_description",
        "source_contour_priority",
    )

    def __init__(
        self,
        *args,
        intent_evidence_classifier: IntentEvidenceAnnotator | None = None,
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
        self._attach_source_contour_provenance(task, result)
        self._attach_historical_archive_provenance(task, request, result)
        await self._finalize_recipe_goal_verification(task, request, result)
        # PublicMapProvenanceWebAdapter invokes the annotator before it evaluates
        # semantic coverage and again for each independently fetched guided page.
        # Repeating it here would duplicate both the model call and Evidence rows.
        return result

    def _discovered_tasks(
        self,
        task: SourceTask,
        fetched,
        request: CollectionRequest,
        collection_id: str,
    ) -> list[SourceTask]:
        """Keep an independent source lane attached to pages reached inside that source."""

        discovered = super()._discovered_tasks(task, fetched, request, collection_id)
        if not task.metadata.get("source_contour"):
            return discovered
        for child in discovered:
            for key in self._source_contour_child_keys:
                if key in task.metadata:
                    child.metadata[key] = task.metadata[key]
        return discovered

    @staticmethod
    def _main_text(content: str, content_type: str | None) -> str:
        """Keep generic document Evidence focused on readable page content."""

        return extract_readable_text(content, content_type)

    @classmethod
    def _attach_source_contour_provenance(
        cls,
        task: SourceTask,
        result: SourceResult,
    ) -> None:
        contour = str(task.metadata.get("source_contour") or "").strip()
        if not contour:
            return
        payload = {
            "version": cls.source_contour_provenance_version,
            "contour_id": contour,
            "planner_version": str(task.metadata.get("source_contour_version") or ""),
            "description": str(task.metadata.get("source_contour_description") or ""),
            "priority": task.metadata.get("source_contour_priority"),
            "navigation_only": True,
            "contour_label_is_evidence": False,
        }
        for observation in result.observations:
            # A source contour describes how ARGUS found the page. It must not change the
            # observation's factual source shape. In particular, a plain document/web_page
            # remains a plain document/web_page so downstream consumers cannot mistake a
            # navigation lane label for a post, complaint or other semantic signal.
            observation.provenance["source_contour"] = dict(payload)
            observation.quality["source_contour_traced"] = True
        for evidence in result.evidence:
            evidence.metadata["source_contour"] = dict(payload)

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
        payload["source_contour_provenance"] = {
            "version": self.source_contour_provenance_version,
            "navigation_label_is_evidence": False,
            "preserved_on_observations": True,
            "preserved_on_evidence": True,
            "preserved_through_depth_crawl": True,
            "document_source_kind": "preserved",
            "document_kind_is_semantic_claim": False,
        }
        payload["historical_archive_page_provenance"] = {
            "version": self.historical_archive_provenance_version,
            "requires_fetched_capture": True,
            "requires_source_backed_territory_match": True,
            "capture_index_is_factual_context": False,
        }
        if self.intent_evidence_classifier is not None:
            payload["intent_evidence_classifier"] = {
                "version": self.intent_evidence_classifier.version,
                "builtin_intents": sorted(self.intent_evidence_classifier.builtin_intents),
                "custom_intents_supported": True,
                "exact_source_excerpt_required": True,
                "deterministic_marker_required_for": sorted(
                    self.intent_evidence_classifier.marker_required_intents
                ),
                "model_output_is_evidence": False,
            }
        return payload