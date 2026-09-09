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


class AtomicContentWebAdapter(IntentEvidenceWebAdapter):
    """Add deterministic atomic publication fallback to the generic web chain.

    The inherited factual web stack already includes ``ContentNavigationMixin`` through
    ``CompressedOfficeAwareGenericWebAdapter``. Reusing that single MRO path keeps item-first
    navigation active without duplicating the mixin in this class. Stronger schema.org/
    microformats observations win. Public-map surfaces are deliberately excluded from this
    HTML fallback. Obvious navigation shells stay crawl surfaces only.
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
        if result.blocked:
            return result
        if self._is_public_map_surface(task, result):
            return result

        page_url = str(fetched.final_url or task.url)
        if self.content_navigation.is_navigation_shell(page_url):
            self._mark_navigation_only(result, reason="url_navigation_shell")
            task.metadata["atomic_content_suppressed"] = "navigation_shell"
            return result

        if self._stronger_atomic_exists(result):
            self._mark_document_container_superseded(
                result,
                reason="structured_atomic_content",
            )
            return result

        extraction = extract_atomic_html_blocks(
            fetched.text,
            content_type=fetched.content_type,
            base_url=page_url,
            max_scan_chars=self.atomic_html_max_scan_chars,
            max_items=self.atomic_html_max_items,
            max_text_chars=self.atomic_html_max_text_chars,
        )
        if extraction.navigation_cards_suppressed:
            self._mark_navigation_only(result, reason="linked_article_listing_cards")
            task.metadata["atomic_navigation_cards_suppressed"] = (
                extraction.navigation_cards_suppressed
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

        self._mark_document_container_superseded(result, reason="html_atomic_content")
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
    def _mark_navigation_only(cls, result: SourceResult, *, reason: str) -> None:
        marked_ids: set[str] = set()
        for observation in result.observations:
            generic_document = (
                observation.entity_type == "document"
                and observation.source_kind == "web_page"
            )
            if not generic_document and observation.entity_type not in _ATOMIC_ENTITY_TYPES:
                continue
            observation.quality["navigation_only"] = True
            observation.quality["message_candidate"] = False
            observation.provenance["content_navigation"] = {
                "version": cls.content_navigation.version,
                "navigation_only": True,
                "reason": reason,
                "classification_is_evidence": False,
            }
            marked_ids.add(observation.observation_id)
        cls._mark_evidence_navigation(result, marked_ids, reason=reason)

    @classmethod
    def _mark_document_container_superseded(
        cls,
        result: SourceResult,
        *,
        reason: str,
    ) -> None:
        marked_ids: set[str] = set()
        for observation in result.observations:
            if not (
                observation.entity_type == "document"
                and observation.source_kind == "web_page"
            ):
                continue
            observation.quality["atomic_container_superseded"] = True
            observation.quality["message_candidate"] = False
            observation.provenance["atomic_delivery"] = {
                "version": "atomic-delivery/1",
                "whole_page_container_only": True,
                "reason": reason,
            }
            marked_ids.add(observation.observation_id)
        for evidence in result.evidence:
            if evidence.observation_id not in marked_ids:
                continue
            evidence.metadata["atomic_delivery"] = {
                "version": "atomic-delivery/1",
                "whole_page_container_only": True,
                "reason": reason,
            }

    @classmethod
    def _mark_evidence_navigation(
        cls,
        result: SourceResult,
        observation_ids: set[str],
        *,
        reason: str,
    ) -> None:
        for evidence in result.evidence:
            if evidence.observation_id not in observation_ids:
                continue
            evidence.metadata["content_navigation"] = {
                "version": cls.content_navigation.version,
                "navigation_only": True,
                "reason": reason,
                "classification_is_evidence": False,
            }

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
            "whole_page_container_after_atomic": "non_messageable",
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
