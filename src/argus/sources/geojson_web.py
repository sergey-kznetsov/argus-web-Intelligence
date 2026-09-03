from __future__ import annotations

import json
import math

from argus.contracts.models import CollectionRequest, Evidence, EvidenceSource, Observation, Point
from argus.history.snapshots import sha256_text
from argus.normalization.identity import stable_evidence_id, stable_observation_id
from argus.sources.base import SourceResult, SourceTask
from argus.sources.schema_web import SchemaAwareSemanticWebAdapter


class GeoJsonAwareWebAdapter(SchemaAwareSemanticWebAdapter):
    """Normalize bounded GeoJSON Point features from the existing JSON dataset path."""

    extractor_version = "geojson-point/1"

    async def extract(
        self,
        task: SourceTask,
        fetched,
        request: CollectionRequest,
    ) -> SourceResult:
        result = await super().extract(task, fetched, request)
        if fetched.blocked:
            return result

        dataset = self._geojson_dataset(result)
        if dataset is None:
            return result
        payload = dataset.data.get("payload")
        features = self._features(payload)
        if features is None:
            return result

        snapshot_id = dataset.provenance.get("snapshot_id")
        if not isinstance(snapshot_id, str) or not snapshot_id:
            return result

        observations: list[Observation] = []
        evidence_items: list[Evidence] = []
        invalid_features = 0
        invalid_points = 0
        non_point_features = 0
        unlocated_features = 0

        for index, feature in enumerate(features):
            if not isinstance(feature, dict) or feature.get("type") != "Feature":
                invalid_features += 1
                continue
            geometry = feature.get("geometry")
            if geometry is None:
                unlocated_features += 1
                continue
            if not isinstance(geometry, dict):
                invalid_features += 1
                continue
            if geometry.get("type") != "Point":
                non_point_features += 1
                continue
            coordinates = geometry.get("coordinates")
            point = GeoJsonAwareWebAdapter._geojson_point(coordinates)
            if point is None:
                invalid_points += 1
                continue

            observation, evidence = self._feature_result(
                feature,
                index=index,
                point=point,
                collection_id=str(task.metadata.get("collection_id", "")),
                request=request,
                source_url=fetched.final_url,
                snapshot_id=snapshot_id,
                dataset_observation_id=dataset.observation_id,
                research_goals=self._research_goals(task),
            )
            observations.append(observation)
            evidence_items.append(evidence)

        dataset.data["geojson_summary"] = {
            "root_type": payload.get("type") if isinstance(payload, dict) else None,
            "features_seen": len(features),
            "point_features_extracted": len(observations),
            "non_point_features_skipped": non_point_features,
            "unlocated_features_skipped": unlocated_features,
            "invalid_features_skipped": invalid_features,
            "invalid_points_skipped": invalid_points,
            "axis_order": "longitude_latitude",
            "max_supported_dimensions": 3,
            "extractor": self.extractor_version,
        }
        result.observations.extend(observations)
        result.evidence.extend(evidence_items)
        return result

    @staticmethod
    def _geojson_dataset(result: SourceResult) -> Observation | None:
        for observation in result.observations:
            if observation.source_kind != "structured_data":
                continue
            payload = observation.data.get("payload")
            if isinstance(payload, dict) and payload.get("type") in {
                "Feature",
                "FeatureCollection",
            }:
                return observation
        return None

    @staticmethod
    def _features(payload: object) -> list[object] | None:
        if not isinstance(payload, dict):
            return None
        root_type = payload.get("type")
        if root_type == "Feature":
            return [payload]
        if root_type == "FeatureCollection":
            features = payload.get("features")
            return features if isinstance(features, list) else None
        return None

    def _feature_result(
        self,
        feature: dict[str, object],
        *,
        index: int,
        point: Point,
        collection_id: str,
        request: CollectionRequest,
        source_url: str,
        snapshot_id: str,
        dataset_observation_id: str,
        research_goals: list[str],
    ) -> tuple[Observation, Evidence]:
        canonical = json.dumps(
            feature,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        content_hash = sha256_text(canonical)
        entity_id = self._feature_id(feature, source_url, index)
        properties = feature.get("properties")
        property_map = properties if isinstance(properties, dict) else {}
        geometry = feature.get("geometry")
        geometry_map = geometry if isinstance(geometry, dict) else {}
        coordinates = geometry_map.get("coordinates")
        dimensions = len(coordinates) if isinstance(coordinates, list) else 0
        observation_id = stable_observation_id(
            collection_id=collection_id,
            source_id=self.source_id,
            entity_type="geospatial_feature",
            entity_id=entity_id,
            source_url=source_url,
            content_hash=content_hash,
        )
        observation = Observation(
            observation_id=observation_id,
            collection_id=collection_id,
            analysis_id=request.analysis_id,
            consumer=request.consumer,
            source=self.source_id,
            source_kind="geojson_point",
            url=source_url,
            entity_type="geospatial_feature",
            entity_id=entity_id,
            title=self._property_string(property_map, "name", "title"),
            text=self._property_string(property_map, "description"),
            data={
                "feature_id": feature.get("id"),
                "properties": property_map,
                "geometry_type": "Point",
                "coordinate_dimensions": dimensions,
                "coordinates": coordinates,
                "research_goals": list(research_goals),
            },
            geo=point,
            content_hash=content_hash,
            provenance={
                "snapshot_id": snapshot_id,
                "dataset_observation_id": dataset_observation_id,
                "feature_index": index,
                "extractor": self.extractor_version,
                "axis_order": "longitude_latitude",
                "crs": "WGS84_CRS84",
                "source_declared": True,
            },
            quality={
                "evidence_backed": True,
                "machine_readable": True,
                "source_declared": True,
                "geospatial_valid": True,
            },
        )
        evidence_text = canonical[:10_000]
        evidence = Evidence(
            evidence_id=stable_evidence_id(
                observation_id=observation.observation_id,
                evidence_type="geojson_point",
                source_url=source_url,
                text=evidence_text,
            ),
            observation_id=observation.observation_id,
            type="geojson_point",
            text=evidence_text,
            source=EvidenceSource(
                provider=self.source_id,
                url=source_url,
                collected_at=observation.collected_at,
                source_id=self.source_id,
            ),
            metadata={
                "snapshot_id": snapshot_id,
                "dataset_observation_id": dataset_observation_id,
                "feature_index": index,
                "extractor": self.extractor_version,
                "axis_order": "longitude_latitude",
                "canonical_sha256": content_hash,
                "evidence_excerpt_truncated": len(canonical) > len(evidence_text),
            },
        )
        return observation, evidence

    @staticmethod
    def _feature_id(feature: dict[str, object], source_url: str, index: int) -> str:
        raw = feature.get("id")
        if isinstance(raw, (str, int, float)) and not isinstance(raw, bool):
            value = str(raw).strip()
            if value:
                return value[:2_000]
        return f"{source_url}#geojson-feature-{index}"

    @staticmethod
    def _geojson_point(coordinates: object) -> Point | None:
        if not isinstance(coordinates, list) or len(coordinates) not in {2, 3}:
            return None
        longitude = GeoJsonAwareWebAdapter._coordinate(coordinates[0])
        latitude = GeoJsonAwareWebAdapter._coordinate(coordinates[1])
        if longitude is None or latitude is None:
            return None
        if len(coordinates) == 3 and GeoJsonAwareWebAdapter._coordinate(coordinates[2]) is None:
            return None
        if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
            return None
        return Point(latitude=latitude, longitude=longitude)

    @staticmethod
    def _coordinate(value: object) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        number = float(value)
        return number if math.isfinite(number) else None

    @staticmethod
    def _property_string(properties: dict[str, object], *names: str) -> str | None:
        for name in names:
            value = properties.get(name)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    async def health(self) -> dict[str, object]:
        payload = dict(await super().health())
        payload["geojson_point_normalization"] = True
        payload["geojson_point_extractor"] = self.extractor_version
        return payload
