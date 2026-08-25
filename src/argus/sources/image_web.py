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
from argus.extraction.images import extract_image_references
from argus.normalization.identity import stable_evidence_id, stable_observation_id
from argus.sources.base import SourceResult, SourceTask
from argus.sources.recipe_web import LifecycleRecipeWebAdapter

_IMAGE_RESEARCH_GOALS = {"historical_context", "historical_images", "images"}


class ImageAwareRecipeWebAdapter(LifecycleRecipeWebAdapter):
    """Add first-class source-declared image references to visual research results."""

    image_max_scan_chars = 750_000
    image_max_items = 50
    image_max_value_chars = 3_000

    async def extract(
        self,
        task: SourceTask,
        fetched,
        request: CollectionRequest,
    ) -> SourceResult:
        result = await super().extract(task, fetched, request)
        if result.blocked:
            return result

        research_goals = self._research_goals(task)
        if not set(research_goals) & _IMAGE_RESEARCH_GOALS:
            return result
        extraction = extract_image_references(
            fetched.text,
            content_type=fetched.content_type,
            base_url=fetched.final_url,
            max_scan_chars=self.image_max_scan_chars,
            max_items=self.image_max_items,
            max_value_chars=self.image_max_value_chars,
        )
        if not extraction.items:
            return result

        collection_id = str(task.metadata.get("collection_id") or "")
        snapshot_id = self._snapshot_id(result)
        for item in extraction.items:
            payload = {
                "image_url": item.image_url,
                "page_url": str(fetched.final_url),
                "declared_by": item.declared_by,
                "alt": item.alt,
                "title": item.title,
                "caption": item.caption,
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
                entity_type="image",
                entity_id=item.image_url,
                source_url=str(fetched.final_url),
                content_hash=content_hash,
            )
            provenance: dict[str, object] = {
                "page_url": str(fetched.final_url),
                "image_reference_extractor": extraction.extractor_version,
                "research_goals": research_goals,
                "source_declared": True,
            }
            if snapshot_id:
                provenance["snapshot_id"] = snapshot_id
            title = item.caption or item.title or item.alt
            text = self._reference_text(item)
            observation = Observation(
                observation_id=observation_id,
                collection_id=collection_id,
                analysis_id=request.analysis_id,
                consumer=request.consumer,
                source=self.source_id,
                source_kind="image_reference",
                url=item.image_url,
                entity_type="image",
                entity_id=item.image_url,
                title=title,
                text=text,
                data={
                    **payload,
                    "research_goals": research_goals,
                    "image_binary_retrieved": False,
                },
                content_hash=content_hash,
                provenance=provenance,
                quality={
                    "evidence_backed": True,
                    "source_declared_image": True,
                    "image_binary_retrieved": False,
                },
            )
            evidence_text = canonical[:10_000]
            evidence = Evidence(
                evidence_id=stable_evidence_id(
                    observation_id=observation_id,
                    evidence_type="image_reference",
                    source_url=str(fetched.final_url),
                    text=evidence_text,
                ),
                observation_id=observation_id,
                type="image_reference",
                text=evidence_text,
                source=EvidenceSource(
                    provider=self.source_id,
                    url=str(fetched.final_url),
                    collected_at=observation.collected_at,
                    source_id=self.source_id,
                ),
                metadata={
                    "image_url": item.image_url,
                    "snapshot_id": snapshot_id,
                    "source_declared": True,
                    "research_goals": research_goals,
                },
            )
            result.observations.append(observation)
            result.evidence.append(evidence)

        if extraction.truncated:
            result.partial = True
            result.errors.append(
                StructuredError(
                    code="IMAGE_REFERENCE_LIMIT_REACHED",
                    message=(
                        "Source-declared image references exceeded the bounded extraction limit "
                        f"of {self.image_max_items} items or {self.image_max_scan_chars} HTML characters."
                    ),
                    retryable=False,
                    source_id=self.source_id,
                )
            )
        return result

    @staticmethod
    def _snapshot_id(result: SourceResult) -> str | None:
        for observation in result.observations:
            value = observation.provenance.get("snapshot_id")
            if isinstance(value, str) and value:
                return value
        return None

    @staticmethod
    def _reference_text(item) -> str | None:
        values = [item.caption, item.title, item.alt]
        text = " | ".join(value for value in values if value)
        return text[:10_000] if text else None

    async def health(self) -> dict[str, object]:
        payload = dict(await super().health())
        payload["image_references"] = {
            "version": "html-image-reference/1",
            "source_declared_only": True,
            "binary_download": False,
            "research_goals": sorted(_IMAGE_RESEARCH_GOALS),
            "max_items": self.image_max_items,
            "max_scan_chars": self.image_max_scan_chars,
        }
        return payload
