from __future__ import annotations

import hashlib
import json
from typing import Any

from argus.contracts.models import CollectionRequest, Evidence, EvidenceSource, Observation
from argus.history.snapshots import SnapshotService
from argus.maps.contracts import MapPlace, MapSearchRequest, MapSearchResult
from argus.maps.overpass import SUPPORTED_CATEGORIES, OverpassMapProvider
from argus.normalization.identity import stable_evidence_id, stable_observation_id
from argus.sources.base import SourceResult, SourceTask


class OverpassSourceAdapter:
    """Expose configured Overpass POI collection through the normal ARGUS source pipeline."""

    source_id = "openstreetmap_overpass"
    intents = set(SUPPORTED_CATEGORIES)

    def __init__(self, provider: OverpassMapProvider, snapshots: SnapshotService) -> None:
        self.provider = provider
        self.snapshots = snapshots

    async def discover(self, request: CollectionRequest) -> list[SourceTask]:
        categories = sorted(set(request.intents) & SUPPORTED_CATEGORIES)
        if not categories:
            return []
        map_request = MapSearchRequest(
            territory=request.territory,
            categories=categories,
            radius_meters=request.territory.radius_meters,
            language=request.constraints.language,
        )
        return [
            SourceTask(
                source_id=self.source_id,
                goal="map_search",
                url=self.provider.endpoint,
                depth=0,
                metadata={"map_request": map_request.model_dump(mode="json")},
            )
        ]

    async def fetch(self, task: SourceTask) -> MapSearchResult:
        raw = task.metadata.get("map_request")
        if not isinstance(raw, dict):
            raise ValueError("Overpass source task is missing map_request metadata")
        return await self.provider.search(MapSearchRequest.model_validate(raw))

    async def extract(
        self,
        task: SourceTask,
        fetched: MapSearchResult,
        request: CollectionRequest,
    ) -> SourceResult:
        collection_id = str(task.metadata.get("collection_id", ""))
        observations: list[Observation] = []
        evidence_items: list[Evidence] = []
        for place in fetched.places:
            observation, evidence = await self._normalize_place(place, collection_id, request)
            observations.append(observation)
            evidence_items.append(evidence)
        return SourceResult(
            observations=observations,
            evidence=evidence_items,
            blocked=fetched.blocked,
            partial=fetched.partial or bool(fetched.errors and fetched.places),
            errors=list(fetched.errors),
        )

    async def normalize(self, result: SourceResult) -> SourceResult:
        return result

    async def health(self) -> dict[str, object]:
        return await self.provider.health()

    async def _normalize_place(
        self,
        place: MapPlace,
        collection_id: str,
        request: CollectionRequest,
    ) -> tuple[Observation, Evidence]:
        facts = {
            "provider_place_id": place.provider_place_id,
            "name": place.name,
            "address": place.address,
            "point": place.point.model_dump(mode="json") if place.point else None,
            "categories": sorted(place.categories),
            "attributes": place.attributes,
        }
        canonical = json.dumps(
            facts,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=self._json_default,
        )
        content_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        snapshot = await self.snapshots.capture(
            self.source_id,
            place.source_url,
            canonical,
            "application/json",
        )
        entity_id = f"{place.provider}:{place.provider_place_id or place.source_url}"
        observation_id = stable_observation_id(
            collection_id=collection_id,
            source_id=self.source_id,
            entity_type="place",
            entity_id=entity_id,
            source_url=place.source_url,
            content_hash=content_hash,
        )
        provenance = {
            "snapshot_id": snapshot.snapshot_id,
            "map_provider": place.provider,
            **place.provenance,
        }
        observation = Observation(
            observation_id=observation_id,
            collection_id=collection_id,
            analysis_id=request.analysis_id,
            consumer=request.consumer,
            source=self.source_id,
            source_kind="map_place",
            url=place.source_url,
            entity_type="place",
            entity_id=entity_id,
            title=place.name,
            text=place.address,
            data={
                "provider_place_id": place.provider_place_id,
                "categories": place.categories,
                "attributes": place.attributes,
            },
            geo=place.point,
            collected_at=place.collected_at,
            content_hash=content_hash,
            provenance=provenance,
            quality={"evidence_backed": True, "map_provider": True},
        )
        evidence_text = self._evidence_text(place)
        evidence = Evidence(
            evidence_id=stable_evidence_id(
                observation_id=observation.observation_id,
                evidence_type="map_place",
                source_url=place.source_url,
                text=evidence_text,
            ),
            observation_id=observation.observation_id,
            type="map_place",
            text=evidence_text,
            source=EvidenceSource(
                provider=self.source_id,
                url=place.source_url,
                collected_at=place.collected_at,
                source_id=self.source_id,
            ),
            metadata={"provenance": provenance},
        )
        return observation, evidence

    @staticmethod
    def _evidence_text(place: MapPlace) -> str:
        rows = [f"name: {place.name}"]
        if place.address:
            rows.append(f"address: {place.address}")
        if place.point:
            rows.append(f"coordinates: {place.point.latitude},{place.point.longitude}")
        if place.categories:
            rows.append("categories: " + ", ".join(place.categories))
        return "\n".join(rows)[:10_000]

    @staticmethod
    def _json_default(value: Any) -> str:
        return str(value)
