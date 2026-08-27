from __future__ import annotations

import json
import math

from argus.contracts.models import (
    CollectionRequest,
    Evidence,
    EvidenceSource,
    Observation,
    Point,
    StructuredError,
)
from argus.history.snapshots import sha256_text
from argus.normalization.identity import stable_evidence_id, stable_observation_id
from argus.sources.base import SourceResult, SourceTask
from argus.sources.geojson_web import GeoJsonAwareWebAdapter


class KmlAwareWebAdapter(GeoJsonAwareWebAdapter):
    """Normalize source-declared KML Placemark/Point facts from bounded XML payloads.

    The structured-data layer already performs the XML parse, entity protection and
    node/depth/string/container bounds. This layer never reparses XML and never
    follows NetworkLink or other KML references.
    """

    kml_extractor_version = "kml-point/1"

    def __init__(self, *args, kml_max_placemarks: int = 1_000, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.kml_max_placemarks = max(1, int(kml_max_placemarks))

    async def extract(
        self,
        task: SourceTask,
        fetched,
        request: CollectionRequest,
    ) -> SourceResult:
        result = await super().extract(task, fetched, request)
        if fetched.blocked:
            return result
        return self._normalize_kml_result(
            result,
            task=task,
            request=request,
            source_url=fetched.final_url,
        )

    def _normalize_kml_result(
        self,
        result: SourceResult,
        *,
        task: SourceTask,
        request: CollectionRequest,
        source_url: str,
    ) -> SourceResult:
        dataset = self._kml_dataset(result)
        if dataset is None:
            return result
        payload = dataset.data.get("payload")
        if not isinstance(payload, dict):
            return result

        snapshot_id = dataset.provenance.get("snapshot_id")
        if not isinstance(snapshot_id, str) or not snapshot_id:
            return result

        placemarks, scan = self._placemarks(payload)
        observations: list[Observation] = []
        evidence_items: list[Evidence] = []
        invalid_points = 0
        unlocated_placemarks = 0
        unsupported_geometry_placemarks = 0

        for index, placemark in enumerate(placemarks[: self.kml_max_placemarks]):
            point_node = self._direct_child(placemark, "Point")
            if point_node is None:
                if self._has_direct_geometry(placemark):
                    unsupported_geometry_placemarks += 1
                else:
                    unlocated_placemarks += 1
                continue

            coordinates_node = self._direct_child(point_node, "coordinates")
            coordinate_text = self._node_text(coordinates_node)
            parsed = self._kml_point(coordinate_text)
            if parsed is None:
                invalid_points += 1
                continue
            point, coordinates = parsed

            observation, evidence = self._placemark_result(
                placemark,
                index=index,
                point=point,
                coordinates=coordinates,
                collection_id=str(task.metadata.get("collection_id", "")),
                request=request,
                source_url=source_url,
                snapshot_id=snapshot_id,
                dataset_observation_id=dataset.observation_id,
                research_goals=self._research_goals(task),
            )
            observations.append(observation)
            evidence_items.append(evidence)

        truncated = len(placemarks) > self.kml_max_placemarks
        dataset.data["kml_summary"] = {
            "placemarks_seen": len(placemarks),
            "point_placemarks_extracted": len(observations),
            "invalid_points_skipped": invalid_points,
            "unlocated_placemarks_skipped": unlocated_placemarks,
            "unsupported_geometry_placemarks_skipped": unsupported_geometry_placemarks,
            "network_links_seen": scan["network_links_seen"],
            "network_links_followed": 0,
            "placemark_limit": self.kml_max_placemarks,
            "truncated": truncated,
            "axis_order": "longitude_latitude",
            "max_supported_dimensions": 3,
            "extractor": self.kml_extractor_version,
        }
        if truncated:
            dataset.quality["partial"] = True
            result.partial = True
            result.errors.append(
                StructuredError(
                    code="KML_EXTRACTION_TRUNCATED",
                    message="KML normalization reached the configured Placemark limit",
                    retryable=False,
                    source_id=self.source_id,
                )
            )

        result.observations.extend(observations)
        result.evidence.extend(evidence_items)
        return result

    @classmethod
    def _kml_dataset(cls, result: SourceResult) -> Observation | None:
        for observation in result.observations:
            if observation.source_kind != "structured_data":
                continue
            if observation.data.get("document_type") != "xml":
                continue
            payload = observation.data.get("payload")
            if isinstance(payload, dict) and cls._local_name(payload.get("tag")) == "kml":
                return observation
        return None

    @classmethod
    def _placemarks(cls, payload: dict[str, object]) -> tuple[list[dict[str, object]], dict[str, int]]:
        placemarks: list[dict[str, object]] = []
        network_links_seen = 0
        stack: list[dict[str, object]] = [payload]
        while stack:
            node = stack.pop()
            name = cls._local_name(node.get("tag"))
            if name == "Placemark":
                placemarks.append(node)
            elif name == "NetworkLink":
                network_links_seen += 1

            children = node.get("children")
            if isinstance(children, list):
                for child in reversed(children):
                    if isinstance(child, dict):
                        stack.append(child)
        return placemarks, {"network_links_seen": network_links_seen}

    def _placemark_result(
        self,
        placemark: dict[str, object],
        *,
        index: int,
        point: Point,
        coordinates: list[float],
        collection_id: str,
        request: CollectionRequest,
        source_url: str,
        snapshot_id: str,
        dataset_observation_id: str,
        research_goals: list[str],
    ) -> tuple[Observation, Evidence]:
        canonical = json.dumps(
            placemark,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        content_hash = sha256_text(canonical)
        placemark_id = self._placemark_id(placemark)
        entity_id = placemark_id or f"{source_url}#kml-placemark-{index}"
        name = self._node_text(self._direct_child(placemark, "name"))
        description = self._node_text(self._direct_child(placemark, "description"))
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
            source_kind="kml_point",
            url=source_url,
            entity_type="geospatial_feature",
            entity_id=entity_id,
            title=name,
            text=description,
            data={
                "placemark_id": placemark_id,
                "geometry_type": "Point",
                "coordinate_dimensions": len(coordinates),
                "coordinates": coordinates,
                "altitude": coordinates[2] if len(coordinates) == 3 else None,
            },
            geo=point,
            content_hash=content_hash,
            provenance={
                "snapshot_id": snapshot_id,
                "dataset_observation_id": dataset_observation_id,
                "placemark_index": index,
                "extractor": self.kml_extractor_version,
                "axis_order": "longitude_latitude",
                "crs": "WGS84",
                "source_declared": True,
                "network_links_followed": False,
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
                evidence_type="kml_point",
                source_url=source_url,
                text=evidence_text,
            ),
            observation_id=observation.observation_id,
            type="kml_point",
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
                "placemark_index": index,
                "extractor": self.kml_extractor_version,
                "axis_order": "longitude_latitude",
                "canonical_sha256": content_hash,
                "evidence_excerpt_truncated": len(canonical) > len(evidence_text),
                "network_links_followed": False,
            },
        )
        return observation, evidence

    @classmethod
    def _direct_child(
        cls,
        node: dict[str, object],
        local_name: str,
    ) -> dict[str, object] | None:
        children = node.get("children")
        if not isinstance(children, list):
            return None
        for child in children:
            if isinstance(child, dict) and cls._local_name(child.get("tag")) == local_name:
                return child
        return None

    @classmethod
    def _has_direct_geometry(cls, placemark: dict[str, object]) -> bool:
        children = placemark.get("children")
        if not isinstance(children, list):
            return False
        geometry_names = {
            "LineString",
            "LinearRing",
            "Polygon",
            "MultiGeometry",
            "Model",
        }
        return any(
            isinstance(child, dict) and cls._local_name(child.get("tag")) in geometry_names
            for child in children
        )

    @staticmethod
    def _node_text(node: dict[str, object] | None) -> str | None:
        if node is None:
            return None
        value = node.get("text")
        if not isinstance(value, str):
            return None
        clean = " ".join(value.split()).strip()
        return clean or None

    @staticmethod
    def _placemark_id(placemark: dict[str, object]) -> str | None:
        attributes = placemark.get("attributes")
        if not isinstance(attributes, dict):
            return None
        raw = attributes.get("id")
        if not isinstance(raw, str):
            return None
        value = raw.strip()
        return value[:2_000] or None

    @staticmethod
    def _kml_point(raw: str | None) -> tuple[Point, list[float]] | None:
        if not raw:
            return None
        tuples = raw.split()
        if len(tuples) != 1:
            return None
        parts = [part.strip() for part in tuples[0].split(",")]
        if len(parts) not in {2, 3} or any(not part for part in parts):
            return None
        values: list[float] = []
        for part in parts:
            try:
                value = float(part)
            except ValueError:
                return None
            if not math.isfinite(value):
                return None
            values.append(value)
        longitude, latitude = values[0], values[1]
        if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
            return None
        return Point(latitude=latitude, longitude=longitude), values

    @staticmethod
    def _local_name(tag: object) -> str:
        if not isinstance(tag, str):
            return ""
        return tag.rsplit("}", 1)[-1]

    async def health(self) -> dict[str, object]:
        payload = dict(await super().health())
        payload["kml_point_normalization"] = True
        payload["kml_network_links_followed"] = False
        payload["kml_max_placemarks"] = self.kml_max_placemarks
        return payload
