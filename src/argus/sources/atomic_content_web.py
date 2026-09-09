from __future__ import annotations

import hashlib
import json

from argus.contracts.models import (
    CollectionRequest,
    Evidence,
    EvidenceSource,
    Observation,
    StructuredError,
)
from argus.extraction.atomic_html import extract_atomic_html_blocks
from argus.normalization.identity import stable_evidence_id, stable_observation_id
from argus.sources.base import SourceResult, SourceTask
from argus.sources.intent_evidence_web import IntentEvidenceWebAdapter
from argus.sources.navigation_web import ContentNavigationMixin

_ATOMIC_ENTITY_TYPES = frozenset({"publication", "post", "comment", "review"})
_INHERITED_PROVENANCE_KEYS = (
    "snapshot_id",
    "source_contour",
    "public_map_source",
    "archive",
)
_INHERITED_QUALITY_KEYS = (
    "source_contour_traced",
    "public_map_source_traced",
    "historical_capture",
)
_INHERITED_EVIDENCE_KEYS = (
    "source_contour",
    "public_map_source",
    "archive",
)


class AtomicContentWebAdapter(ContentNavigationMixin, IntentEvidenceWebAdapter):
    """Add deterministic atomic publication fallback to the generic web chain.

    Stronger schema.org/microformats observations win. Public-map surfaces are deliberately
    excluded from this HTML fallback: maps remain information sources unless their existing
    structured extractors expose an actual review/comment/post entity. Obvious navigation
    shells stay crawl surfaces only; likely item links are ranked before bounded fan-out.
    """

    atomic_html_max_scan_chars = 750_000
    atomic_html_max_items = 20
    atomic_html_max_text_chars = 50_000

    async def extract(
        self,
        task: SourceTask,
        fetched,
        request: CollectionRequest,
    ) -> SourceResult:
        result = await super().extract(task, fetched, request)
        if result.blocked or self._stronger_atomic_exists(result):
            return result
        if self._is_public_map_surface(task, result):
            return result
        if self.content_navigation.is_navigation_shell(str(fetched.final_url or task.url)):
            task.metadata["atomic_content_suppressed"] = "navigation_shell"
            return result

        extraction = extract_atomic_html_blocks(
            fetched.text,
            content_type=fetched.content_type,
            base_url=str(fetched.final_url or task.url),
            max_scan_chars=self.atomic_html_max_scan_chars,
            max_items=self.atomic_html_max_items,
            max_text_chars=self.atomic_html_max_text_chars,
        )
        if not extraction.items:
            return result

        collection_id = str(task.metadata.get("collection_id") or "").strip()
        inherited_provenance, inherited_quality, inherited_evidence = self._inherited_context(
            result
        )
        for item in extraction.items:
            payload = {
                "title": item.title,
                "text": item.text,
                "declared_time": item.declared_time,
                "selector_kind": item.selector_kind,
                "page_url": str(fetched.final_url),
                "extractor_version": extraction.extractor_version,
            }
            canonical = json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            content_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            observation_id = stable_observation_id(
                collection_id=collection_id,
                source_id=self.source_id,
                entity_type="publication",
                entity_id=f"html-atomic:{content_hash[:20]}",
                source_url=str(fetched.final_url),
                content_hash=content_hash,
            )
            provenance = {
                **inherited_provenance,
                "atomic_html": {
                    "version": extraction.extractor_version,
                    "selector_kind": item.selector_kind,
                    "source_declared_semantic_container": True,
                    "model_generated": False,
                    "navigation_label_is_evidence": False,
                    "page_url": str(fetched.final_url),
                },
            }
            quality = {
                **inherited_quality,
                "evidence_backed": True,
                "atomic_content": True,
                "source_declared_semantic_container": True,
                "whole_page_document": False,
            }
            observation = Observation(
                observation_id=observation_id,
                collection_id=collection_id,
                analysis_id=request.analysis_id,
                consumer=request.consumer,
                source=self.source_id,
                source_kind="html_atomic",
                url=str(fetched.final_url),
                entity_type="publication",
                entity_id=f"html-atomic:{content_hash[:20]}",
                title=item.title,
                text=item.text,
                data={
                    "declared_time": item.declared_time,
                    "selector_kind": item.selector_kind,
                    "extractor_version": extraction.extractor_version,
                    "research_goal": task.goal,
                },
                content_hash=content_hash,
                provenance=provenance,
                quality=quality,
            )
            evidence_text = f"{item.title}\n{item.text}"[:10_000]
            evidence = Evidence(
                evidence_id=stable_evidence_id(
                    observation_id=observation_id,
                    evidence_type="html_atomic_excerpt",
                    source_url=str(fetched.final_url),
                    text=evidence_text,
                ),
                observation_id=observation_id,
                type="html_atomic_excerpt",
                text=evidence_text,
                source=EvidenceSource(
                    provider=self.source_id,
                    url=str(fetched.final_url),
                    collected_at=observation.collected_at,
                    source_id=self.source_id,
                ),
                metadata={
                    **inherited_evidence,
                    "atomic_html": {
                        "version": extraction.extractor_version,
                        "selector_kind": item.selector_kind,
                        "source_declared_semantic_container": True,
                        "model_generated": False,
                    },
                    "declared_time": item.declared_time,
                    "page_url": str(fetched.final_url),
                },
            )
            result.observations.append(observation)
            result.evidence.append(evidence)

        if extraction.truncated:
            result.partial = True
            result.errors.append(
                StructuredError(
                    code="ATOMIC_HTML_LIMIT_REACHED",
                    message=(
                        "Source-declared atomic HTML containers exceeded the bounded "
                        f"extraction limit of {self.atomic_html_max_items} items."
                    ),
                    retryable=False,
                    source_id=self.source_id,
                )
            )
        return result

    @staticmethod
    def _stronger_atomic_exists(result: SourceResult) -> bool:
        return any(
            observation.entity_type in _ATOMIC_ENTITY_TYPES
            and bool((observation.text or "").strip())
            for observation in result.observations
        )

    @staticmethod
    def _is_public_map_surface(task: SourceTask, result: SourceResult) -> bool:
        if task.metadata.get("public_map_provider") or task.metadata.get("public_map_direct_navigation"):
            return True
        return any(
            bool(observation.provenance.get("public_map_source"))
            for observation in result.observations
        )

    @classmethod
    def _inherited_context(
        cls,
        result: SourceResult,
    ) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
        provenance: dict[str, object] = {}
        quality: dict[str, object] = {}
        evidence_metadata: dict[str, object] = {}

        for observation in result.observations:
            for key in _INHERITED_PROVENANCE_KEYS:
                if key in observation.provenance and key not in provenance:
                    provenance[key] = observation.provenance[key]
            for key in _INHERITED_QUALITY_KEYS:
                if key in observation.quality and key not in quality:
                    quality[key] = observation.quality[key]

        for evidence in result.evidence:
            for key in _INHERITED_EVIDENCE_KEYS:
                if key in evidence.metadata and key not in evidence_metadata:
                    evidence_metadata[key] = evidence.metadata[key]

        return provenance, quality, evidence_metadata

    async def health(self) -> dict[str, object]:
        payload = dict(await super().health())
        payload["atomic_html"] = {
            "version": "html-atomic/2",
            "source_declared_semantics_only": True,
            "css_class_heuristics": False,
            "linked_heading_listing_cards": "navigation_only",
            "obvious_navigation_shells": "navigation_only",
            "source_contour_as_semantic_label": False,
            "public_map_fallback": False,
            "max_items": self.atomic_html_max_items,
            "max_scan_chars": self.atomic_html_max_scan_chars,
            "max_text_chars": self.atomic_html_max_text_chars,
        }
        payload["content_navigation"] = {
            "version": self.content_navigation.version,
            "item_first_bounded_fanout": True,
            "ranking_is_evidence": False,
        }
        return payload


__all__ = ["AtomicContentWebAdapter"]
