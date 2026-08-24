from __future__ import annotations

from argus.contracts.models import CollectionRequest, Evidence, Observation
from argus.extraction.jsonld import JsonLdExtraction
from argus.extraction.microdata import MicrodataExtraction
from argus.normalization.identity import stable_evidence_id, stable_observation_id
from argus.normalization.schema_types import classify_schema_entity
from argus.sources.semantic_web import SemanticWebAdapter


class SchemaAwareSemanticWebAdapter(SemanticWebAdapter):
    """Normalize explicit schema.org types without changing source-declared payloads."""

    def _json_ld_observations(
        self,
        extraction: JsonLdExtraction,
        *,
        collection_id: str,
        request: CollectionRequest,
        source_url: str,
        snapshot_id: str,
        research_goals: list[str],
    ) -> tuple[list[Observation], list[Evidence]]:
        observations, evidence = super()._json_ld_observations(
            extraction,
            collection_id=collection_id,
            request=request,
            source_url=source_url,
            snapshot_id=snapshot_id,
            research_goals=research_goals,
        )
        for entity, observation in zip(extraction.entities, observations, strict=False):
            entity_type, schema_types = classify_schema_entity(
                entity.data.get("@type"),
                context_hints=entity.context_hints,
            )
            self._apply_schema_type(
                observation,
                evidence,
                entity_type=entity_type,
                schema_types=schema_types,
                context_hints=list(entity.context_hints),
                source_url=source_url,
            )
        return observations, evidence

    def _microdata_observations(
        self,
        extraction: MicrodataExtraction,
        *,
        collection_id: str,
        request: CollectionRequest,
        page_url: str,
        snapshot_id: str,
        research_goals: list[str],
    ) -> tuple[list[Observation], list[Evidence]]:
        observations, evidence = super()._microdata_observations(
            extraction,
            collection_id=collection_id,
            request=request,
            page_url=page_url,
            snapshot_id=snapshot_id,
            research_goals=research_goals,
        )
        for item, observation in zip(extraction.items, observations, strict=False):
            entity_type, schema_types = classify_schema_entity(item.item_types)
            self._apply_schema_type(
                observation,
                evidence,
                entity_type=entity_type,
                schema_types=schema_types,
                context_hints=[],
                source_url=page_url,
            )
        return observations, evidence

    def _apply_schema_type(
        self,
        observation: Observation,
        evidence_items: list[Evidence],
        *,
        entity_type: str,
        schema_types: list[str],
        context_hints: list[str],
        source_url: str,
    ) -> None:
        normalization = {
            "recognized_types": schema_types,
            "normalized_entity_type": entity_type,
            "context_hints": context_hints,
            "remote_vocabularies_resolved": False,
        }
        observation.provenance["schema_type_normalization"] = normalization
        observation.quality["schema_org_typed"] = bool(schema_types)
        observation.quality["normalized_entity_type"] = entity_type

        old_id = observation.observation_id
        if entity_type != observation.entity_type:
            observation.entity_type = entity_type
            observation.observation_id = stable_observation_id(
                collection_id=observation.collection_id,
                source_id=self.source_id,
                entity_type=entity_type,
                entity_id=observation.entity_id,
                source_url=source_url,
                content_hash=observation.content_hash,
            )

        for evidence in evidence_items:
            if evidence.observation_id != old_id:
                continue
            evidence.metadata["schema_type_normalization"] = normalization
            if observation.observation_id != old_id:
                evidence.observation_id = observation.observation_id
                evidence.evidence_id = stable_evidence_id(
                    observation_id=observation.observation_id,
                    evidence_type=evidence.type,
                    source_url=evidence.source.url,
                    text=evidence.text,
                )

    async def health(self) -> dict[str, object]:
        payload = dict(await super().health())
        payload["schema_org_type_normalization"] = True
        return payload
