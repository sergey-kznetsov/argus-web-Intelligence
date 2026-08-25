from __future__ import annotations

import hashlib
import json
from typing import Any

from argus.contracts.models import (
    CollectionRequest,
    Evidence,
    EvidenceSource,
    Observation,
    StructuredError,
)
from argus.geocoding.contracts import GeocodeProvider
from argus.history.snapshots import SnapshotService
from argus.maps.contracts import MapPlace, MapSearchRequest, MapSearchResult
from argus.maps.overpass import SUPPORTED_CATEGORIES, OverpassMapProvider
from argus.normalization.identity import stable_evidence_id, stable_observation_id
from argus.sources.base import SourceResult, SourceTask

_AREA_RESEARCH_INTENTS = {
    "reviews",
    "comments",
    "complaints",
    "discussions",
    "public_mentions",
    "local_news",
    "incidents",
    "historical_context",
}


class OverpassSourceAdapter:
    """Expose configured Overpass POI collection through the normal ARGUS source pipeline."""

    source_id = "openstreetmap_overpass"
    intents = set(SUPPORTED_CATEGORIES) | {"*"}

    def __init__(
        self,
        provider: OverpassMapProvider,
        snapshots: SnapshotService,
        geocoder: GeocodeProvider | None = None,
    ) -> None:
        self.provider = provider
        self.snapshots = snapshots
        self.geocoder = geocoder

    async def discover(self, request: CollectionRequest) -> list[SourceTask]:
        categories = sorted(set(request.intents) & SUPPORTED_CATEGORIES)
        tasks: list[SourceTask] = []
        for category in categories:
            map_request = MapSearchRequest(
                territory=request.territory,
                categories=[category],
                radius_meters=request.territory.radius_meters,
                language=request.constraints.language,
            )
            tasks.append(
                SourceTask(
                    source_id=self.source_id,
                    goal=category,
                    url=self.provider.endpoint,
                    depth=0,
                    metadata={"map_request": map_request.model_dump(mode="json")},
                    task_key=f"{self.source_id}:{self.provider.endpoint}:{category}",
                )
            )

        if set(request.intents) & _AREA_RESEARCH_INTENTS:
            area_request = MapSearchRequest(
                territory=request.territory,
                categories=["named_feature"],
                radius_meters=request.territory.radius_meters,
                limit=100,
                language=request.constraints.language,
                metadata={"purpose": "area_entity_inventory"},
            )
            tasks.append(
                SourceTask(
                    source_id=self.source_id,
                    goal="area_entity_inventory",
                    url=self.provider.endpoint,
                    depth=0,
                    metadata={
                        "map_request": area_request.model_dump(mode="json"),
                        "research_goals": list(request.intents),
                        "area_entity_inventory": True,
                    },
                    task_key=f"{self.source_id}:{self.provider.endpoint}:area_entity_inventory",
                )
            )
        return tasks

    async def fetch(self, task: SourceTask) -> MapSearchResult:
        raw = task.metadata.get("map_request")
        if not isinstance(raw, dict):
            raise ValueError("Overpass source task is missing map_request metadata")
        map_request = MapSearchRequest.model_validate(raw)
        if map_request.territory.point is None:
            resolved, failure = await self._resolve_point(task, map_request)
            if failure is not None:
                return failure
            map_request = resolved
        return await self.provider.search(map_request)

    async def extract(
        self,
        task: SourceTask,
        fetched: MapSearchResult,
        request: CollectionRequest,
    ) -> SourceResult:
        collection_id = str(task.metadata.get("collection_id", ""))
        geocoding_raw = task.metadata.get("geocoding")
        geocoding = geocoding_raw if isinstance(geocoding_raw, dict) else None
        observations: list[Observation] = []
        evidence_items: list[Evidence] = []
        for place in fetched.places:
            observation, evidence = await self._normalize_place(
                place,
                collection_id,
                request,
                geocoding=geocoding,
            )
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
        result = await self.provider.health()
        result["geocoding"] = (
            await self.geocoder.health() if self.geocoder is not None else {"status": "not_configured"}
        )
        return result

    async def _resolve_point(
        self,
        task: SourceTask,
        map_request: MapSearchRequest,
    ) -> tuple[MapSearchRequest, MapSearchResult | None]:
        if self.geocoder is None:
            return map_request, MapSearchResult(
                provider=self.provider.provider_id,
                errors=[
                    StructuredError(
                        code="GEOCODING_NOT_CONFIGURED",
                        message=(
                            "Map search received an address without coordinates and no geocoding "
                            "provider is configured"
                        ),
                        retryable=False,
                        source_id=f"map:{self.provider.provider_id}",
                    )
                ],
            )

        query = map_request.territory.address or map_request.territory.city
        if not query:
            return map_request, MapSearchResult(
                provider=self.provider.provider_id,
                errors=[
                    StructuredError(
                        code="GEOCODING_QUERY_REQUIRED",
                        message="Map search requires coordinates, address, or city for geocoding",
                        retryable=False,
                        source_id=f"map:{self.provider.provider_id}",
                    )
                ],
            )

        result = await self.geocoder.search(
            query,
            limit=1,
            language=map_request.language,
        )
        if result.blocked or result.errors or not result.candidates:
            errors = list(result.errors)
            if not errors:
                errors.append(
                    StructuredError(
                        code="GEOCODING_NO_RESULTS",
                        message="Geocoding returned no coordinate candidates",
                        retryable=False,
                        source_id=f"geocoding:{result.provider}",
                    )
                )
            return map_request, MapSearchResult(
                provider=self.provider.provider_id,
                blocked=result.blocked,
                errors=errors,
            )

        candidate = result.candidates[0]
        task.metadata["geocoding"] = {
            "provider": candidate.provider,
            "provider_place_id": candidate.provider_place_id,
            "display_name": candidate.display_name,
            "point": candidate.point.model_dump(mode="json"),
            "source_url": candidate.source_url,
            "importance": candidate.importance,
            "provenance": candidate.provenance,
        }
        territory = map_request.territory.model_copy(update={"point": candidate.point})
        return map_request.model_copy(update={"territory": territory}), None

    async def _normalize_place(
        self,
        place: MapPlace,
        collection_id: str,
        request: CollectionRequest,
        *,
        geocoding: dict[str, Any] | None = None,
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
            collection_id=collection_id,
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
        provenance: dict[str, Any] = {
            "snapshot_id": snapshot.snapshot_id,
            "map_provider": place.provider,
            **place.provenance,
        }
        if geocoding is not None:
            provenance["geocoding"] = geocoding
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
                "name": place.name,
                "address": place.address,
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
