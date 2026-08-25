from __future__ import annotations

from datetime import datetime
import math

from argus.contracts.models import CollectionRequest, Evidence, Observation, Point
from argus.extraction.jsonld import JsonLdExtraction
from argus.extraction.microdata import MicrodataExtraction
from argus.normalization.identity import stable_evidence_id, stable_observation_id
from argus.normalization.schema_types import classify_schema_entity
from argus.sources.semantic_web import SemanticWebAdapter


class SchemaAwareSemanticWebAdapter(SemanticWebAdapter):
    """Normalize explicit schema.org types and factual fields without inference."""

    _TEXT_FIELDS = {
        "review": ("reviewBody", "description"),
        "publication": ("articleBody", "text", "description"),
        "comment": ("text", "description"),
        "dataset": ("description",),
        "event": ("description",),
        "organization": ("description",),
        "person": ("description",),
        "place": ("description",),
        "product": ("description",),
        "service": ("description",),
    }

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
            if schema_types:
                self._apply_schema_fields(
                    observation,
                    evidence,
                    entity_type=entity_type,
                    value_getter=lambda name, data=entity.data: self._json_ld_string(data, name),
                )
                if entity_type == "review":
                    self._apply_review_normalization(
                        observation,
                        evidence,
                        rating=self._json_ld_rating(entity.data.get("reviewRating")),
                        author=self._json_ld_label_value(entity.data.get("author")),
                        item_reviewed=self._json_ld_reference(entity.data.get("itemReviewed")),
                    )
                self._apply_json_ld_geo(
                    observation,
                    evidence,
                    data=entity.data,
                    schema_types=schema_types,
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
            if schema_types:
                self._apply_schema_fields(
                    observation,
                    evidence,
                    entity_type=entity_type,
                    value_getter=lambda name, props=item.properties: self._microdata_string(
                        props, name
                    ),
                )
                if entity_type == "review":
                    self._apply_review_normalization(
                        observation,
                        evidence,
                        rating=self._microdata_rating(item.properties),
                        author=self._microdata_label_value(item.properties, "author"),
                        item_reviewed=self._microdata_reference(item.properties, "itemReviewed"),
                    )
                self._apply_microdata_geo(
                    observation,
                    evidence,
                    properties=item.properties,
                    schema_types=schema_types,
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

    def _apply_schema_fields(
        self,
        observation: Observation,
        evidence_items: list[Evidence],
        *,
        entity_type: str,
        value_getter,
    ) -> None:
        text_field: str | None = None
        normalized_text: str | None = None
        for candidate in self._TEXT_FIELDS.get(entity_type, ("description",)):
            value = value_getter(candidate)
            if value:
                text_field = candidate
                normalized_text = value
                break

        published_value = value_getter("datePublished")
        published_at = self._datetime(published_value)
        if normalized_text is not None:
            observation.text = normalized_text
        if published_at is not None:
            observation.published_at = published_at

        field_normalization = {
            "text_field": text_field,
            "published_at_field": "datePublished" if published_at is not None else None,
            "source_declared_only": True,
        }
        observation.provenance["schema_field_normalization"] = field_normalization
        for evidence in evidence_items:
            if evidence.observation_id == observation.observation_id:
                evidence.metadata["schema_field_normalization"] = field_normalization

    def _apply_review_normalization(
        self,
        observation: Observation,
        evidence_items: list[Evidence],
        *,
        rating: dict[str, object] | None,
        author: str | None,
        item_reviewed: dict[str, str] | None,
    ) -> None:
        if rating is None and author is None and item_reviewed is None:
            return
        normalization: dict[str, object] = {
            "source_declared_only": True,
            "rating": rating,
            "author": author,
            "item_reviewed": item_reviewed,
        }
        observation.provenance["schema_review_normalization"] = normalization
        observation.quality["schema_review_facts"] = True
        observation.quality["schema_review_rating_valid"] = bool(
            rating is not None and rating.get("valid") is True
        )
        for evidence in evidence_items:
            if evidence.observation_id == observation.observation_id:
                evidence.metadata["schema_review_normalization"] = normalization

    def _apply_json_ld_geo(
        self,
        observation: Observation,
        evidence_items: list[Evidence],
        *,
        data: dict[str, object],
        schema_types: list[str],
    ) -> None:
        latitude: object | None = None
        longitude: object | None = None
        source_field: str | None = None

        geo = data.get("geo")
        if isinstance(geo, dict) and ("latitude" in geo or "longitude" in geo):
            latitude = geo.get("latitude")
            longitude = geo.get("longitude")
            source_field = "geo"
        elif "latitude" in data or "longitude" in data:
            latitude = data.get("latitude")
            longitude = data.get("longitude")
            source_field = "latitude_longitude"
        elif "GeoCoordinates" not in schema_types:
            return

        self._apply_geo_values(
            observation,
            evidence_items,
            latitude=latitude,
            longitude=longitude,
            source_field=source_field or "GeoCoordinates",
        )

    def _apply_microdata_geo(
        self,
        observation: Observation,
        evidence_items: list[Evidence],
        *,
        properties: dict[str, list[object]],
        schema_types: list[str],
    ) -> None:
        has_coordinates = "latitude" in properties or "longitude" in properties
        if not has_coordinates and "GeoCoordinates" not in schema_types:
            return
        self._apply_geo_values(
            observation,
            evidence_items,
            latitude=self._first_property(properties, "latitude"),
            longitude=self._first_property(properties, "longitude"),
            source_field="latitude_longitude",
        )

    def _apply_geo_values(
        self,
        observation: Observation,
        evidence_items: list[Evidence],
        *,
        latitude: object | None,
        longitude: object | None,
        source_field: str,
    ) -> None:
        point = self._point(latitude, longitude)
        normalization = {
            "source_field": source_field,
            "source_declared": True,
            "valid_point": point is not None,
            "geocoding_used": False,
        }
        if point is not None:
            observation.geo = point
        observation.provenance["schema_geo_normalization"] = normalization
        observation.quality["geospatial_valid"] = point is not None
        for evidence in evidence_items:
            if evidence.observation_id == observation.observation_id:
                evidence.metadata["schema_geo_normalization"] = normalization

    @classmethod
    def _json_ld_rating(cls, value: object) -> dict[str, object] | None:
        if value is None:
            return None
        if isinstance(value, list):
            value = value[0] if value else None
        if isinstance(value, dict):
            rating_value = cls._number(value.get("ratingValue"))
            best = cls._number(value.get("bestRating"))
            worst = cls._number(value.get("worstRating"))
        else:
            rating_value = cls._number(value)
            best = None
            worst = None
        return cls._rating_payload(rating_value, best, worst)

    @classmethod
    def _microdata_rating(cls, properties: dict[str, list[object]]) -> dict[str, object] | None:
        rating_value = cls._number(cls._first_property(properties, "ratingValue"))
        best = cls._number(cls._first_property(properties, "bestRating"))
        worst = cls._number(cls._first_property(properties, "worstRating"))
        return cls._rating_payload(rating_value, best, worst)

    @staticmethod
    def _rating_payload(
        value: float | None,
        best: float | None,
        worst: float | None,
    ) -> dict[str, object] | None:
        if value is None and best is None and worst is None:
            return None
        valid = value is not None
        if best is not None and worst is not None:
            valid = valid and best > worst and worst <= value <= best
        elif best is not None:
            valid = valid and value <= best
        elif worst is not None:
            valid = valid and value >= worst
        return {
            "value": value,
            "best": best,
            "worst": worst,
            "valid": bool(valid),
        }

    @classmethod
    def _json_ld_label_value(cls, value: object) -> str | None:
        if isinstance(value, list):
            for item in value:
                label = cls._json_ld_label_value(item)
                if label:
                    return label
            return None
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            for key in ("name", "alternateName"):
                label = value.get(key)
                if isinstance(label, str) and label.strip():
                    return label.strip()
        return None

    @classmethod
    def _json_ld_reference(cls, value: object) -> dict[str, str] | None:
        if isinstance(value, list):
            for item in value:
                reference = cls._json_ld_reference(item)
                if reference:
                    return reference
            return None
        if isinstance(value, str) and value.strip():
            return {"value": value.strip()}
        if not isinstance(value, dict):
            return None
        result: dict[str, str] = {}
        for output_key, source_key in (("name", "name"), ("id", "@id"), ("url", "url")):
            candidate = value.get(source_key)
            if isinstance(candidate, str) and candidate.strip():
                result[output_key] = candidate.strip()
        return result or None

    @classmethod
    def _microdata_label_value(
        cls,
        properties: dict[str, list[object]],
        name: str,
    ) -> str | None:
        value = cls._first_property(properties, name)
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    @classmethod
    def _microdata_reference(
        cls,
        properties: dict[str, list[object]],
        name: str,
    ) -> dict[str, str] | None:
        value = cls._first_property(properties, name)
        if isinstance(value, str) and value.strip():
            return {"value": value.strip()}
        if not isinstance(value, dict):
            return None
        result: dict[str, str] = {}
        item_id = value.get("itemid")
        if isinstance(item_id, str) and item_id.strip():
            result["id"] = item_id.strip()
        item_types = value.get("itemtype")
        if isinstance(item_types, list) and item_types:
            result["type"] = str(item_types[0])
        return result or None

    @staticmethod
    def _json_ld_string(data: dict[str, object], name: str) -> str | None:
        value = data.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str) and item.strip():
                    return item.strip()
        return None

    @staticmethod
    def _microdata_string(properties: dict[str, list[object]], name: str) -> str | None:
        for value in properties.get(name, []):
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @staticmethod
    def _first_property(properties: dict[str, list[object]], name: str) -> object | None:
        values = properties.get(name, [])
        return values[0] if values else None

    @classmethod
    def _point(cls, latitude: object | None, longitude: object | None) -> Point | None:
        lat = cls._number(latitude)
        lon = cls._number(longitude)
        if lat is None or lon is None:
            return None
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return None
        return Point(latitude=lat, longitude=lon)

    @staticmethod
    def _number(value: object | None) -> float | None:
        if isinstance(value, list):
            value = value[0] if value else None
        if isinstance(value, bool) or value is None:
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    @staticmethod
    def _datetime(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None

    async def health(self) -> dict[str, object]:
        payload = dict(await super().health())
        payload["schema_org_type_normalization"] = True
        payload["schema_org_field_normalization"] = True
        payload["schema_org_review_normalization"] = True
        payload["schema_org_geo_normalization"] = True
        return payload
