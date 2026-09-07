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
        # The area inventory is a geometric tool. If the caller supplied only text and no
        # geocoder exists, silently leave this source out instead of turning an otherwise
        # valid web-only collection into a degraded map failure. Geo Analyzer/Kraken supply
        # coordinates for radius analyses, so their real 1000 m contour still executes.
        if request.territory.point is None and self.geocoder is None:
            return []

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
                        message="Geocoder did not resolve the requested territory",
                        retryable=False,
                        source_id=f"map:{self.provider.provider_id}",
                    )
                )
            return map_request, MapSearchResult(
                provider=self.provider.provider_id,
                blocked=result.blocked,
                partial=bool(result.candidates),
                errors=errors,
            )

        candidate = result.candidates[0]
        territory = map_request.territory.model_copy(
            update={
                "point": candidate.point,
                "metadata": {
                    **map_request.territory.metadata,
                    "geocoding_provider": result.provider,
                    "geocoding_display_name": candidate.display_name,
                },
            }
        )
        task.metadata["geocoding"] = {
            "provider": result.provider,
            "display_name": candidate.display_name,
            "latitude": candidate.point.latitude,
            "longitude": candidate.point.longitude,
        }
        return map_request.model_copy(update={"territory": territory}), None

    async def _normalize_place(
        self,
        place: MapPlace,
        collection_id: str,
        request: CollectionRequest,
        *,
        geocoding: dict[str, object] | None,
    ) -> tuple[Observation, Evidence]:
        payload = place.model_dump(mode="json")
        payload["name"] = place.name
        payload["address"] = place.address
        payload["categories"] = list(place.categories)
        payload["provider"] = self.provider.provider_id
        payload["provider_id"] = place.provider_id
        if geocoding is not None:
            payload["geocoding"] = dict(geocoding)

        source_url = place.url or self.provider.endpoint
        identity_seed = place.provider_id or source_url or json.dumps(payload, sort_keys=True)
        entity_id = f"{self.provider.provider_id}:{identity_seed}"
        geo = place.point
        observation_id = stable_observation_id(
            collection_id,
            self.source_id,
            "map_place",
            entity_id,
            source_url,
        )
        evidence_id = stable_evidence_id(observation_id, "map_payload", source_url)
        content_hash = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        provenance = {
            "provider": self.provider.provider_id,
            "provider_id": place.provider_id,
            "request_radius_meters": (
                request.territory.radius_meters
                if request.territory.radius_meters is not None
                else 1000
            ),
        }
        if geocoding is not None:
            provenance["geocoding"] = dict(geocoding)
        observation = Observation(
            observation_id=observation_id,
            collection_id=collection_id,
            analysis_id=request.analysis_id,
            consumer=request.consumer,
            source=self.source_id,
            source_kind="map_place",
            url=source_url,
            entity_type="place",
            entity_id=entity_id,
            title=place.name,
            text="\n".join(item for item in [place.name, place.address] if item),
            data=payload,
            geo=geo,
            content_hash=content_hash,
            provenance=provenance,
            quality={"source_backed_geo": geo is not None},
        )
        evidence = Evidence(
            evidence_id=evidence_id,
            observation_id=observation_id,
            type="map_payload",
            source=EvidenceSource(
                source_id=self.source_id,
                url=source_url,
                title=place.name,
                accessed_at=observation.observed_at,
            ),
            excerpt=json.dumps(payload, ensure_ascii=False)[:4000],
            metadata={
                "provider": self.provider.provider_id,
                "provider_id": place.provider_id,
                "source_backed_geo": geo is not None,
            },
        )
        if geocoding is not None:
            evidence.metadata["geocoding"] = dict(geocoding)
        return observation, evidence