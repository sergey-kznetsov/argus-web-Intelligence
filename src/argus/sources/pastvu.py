from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode, urlsplit

from argus.contracts.models import (
    CollectionRequest,
    Evidence,
    EvidenceSource,
    Observation,
    Point,
    StructuredError,
)
from argus.crawler.fast.runtime import FastCrawlerRuntime
from argus.crawler.models import FetchResult
from argus.geocoding.contracts import GeocodeProvider
from argus.history.snapshots import SnapshotService
from argus.normalization.identity import stable_evidence_id, stable_observation_id
from argus.sources.base import SourceResult, SourceTask


class PastVuHistoricalAdapter:
    """Collect source-declared historical photo references nearest a research point.

    PastVu exposes a public API specifically for nearby historical photographs. This adapter
    uses that API directly instead of relying on search-engine snippets. Address geocoding is
    optional; when no point can be resolved, the adapter emits no direct task and ordinary
    ARGUS discovery remains available as a fallback.
    """

    source_id = "pastvu_historical"
    intents = {"historical_images"}
    api_host = "api.pastvu.com"
    api_endpoint = "https://api.pastvu.com/api2"
    item_origin = "https://pastvu.com"
    image_origin = "https://img.pastvu.com"
    api_method = "photo.giveNearestPhotos"
    extractor_version = "pastvu-nearest-photos/1"
    max_items = 30
    default_items = 20
    default_distance_meters = 1_000
    max_distance_meters = 10_000
    historical_min_age_years = 5

    def __init__(
        self,
        fast: FastCrawlerRuntime,
        snapshots: SnapshotService,
        *,
        geocoder: GeocodeProvider | None = None,
    ) -> None:
        self.fast = fast
        self.snapshots = snapshots
        self.geocoder = geocoder

    async def discover(self, request: CollectionRequest) -> list[SourceTask]:
        if "historical_images" not in request.intents:
            return []

        point = request.territory.point
        geocoding: dict[str, object] | None = None
        if point is None:
            point, geocoding = await self._resolve_point(request)
        if point is None:
            return []

        distance = self._distance(request)
        year2 = datetime.now(UTC).year - self.historical_min_age_years
        params = {
            "geo": [point.latitude, point.longitude],
            "distance": distance,
            "year2": year2,
            "type": "photo",
            "limit": self.default_items,
        }
        url = self.api_endpoint + "?" + urlencode(
            {
                "method": self.api_method,
                "params": json.dumps(
                    params,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            }
        )
        metadata: dict[str, object] = {
            "pastvu_api_method": self.api_method,
            "pastvu_query_point": point.model_dump(mode="json"),
            "pastvu_distance_meters": distance,
            "pastvu_year2": year2,
            "pastvu_limit": self.default_items,
            "research_goals": ["historical_images"],
            "source_owned_navigation": True,
            "source_declared_historical_media": True,
        }
        if geocoding is not None:
            metadata["geocoding"] = geocoding
        return [
            SourceTask(
                source_id=self.source_id,
                goal="historical_images",
                url=url,
                metadata=metadata,
                task_key=(
                    f"{self.source_id}:{point.latitude:.6f}:{point.longitude:.6f}:"
                    f"{distance}:{year2}"
                ),
            )
        ]

    async def fetch(self, task: SourceTask) -> FetchResult:
        parsed = urlsplit(task.url)
        if parsed.scheme != "https" or (parsed.hostname or "").casefold() != self.api_host:
            raise ValueError("PastVu historical task must target the official PastVu API")
        return await self.fast.fetch(task.url)

    async def extract(
        self,
        task: SourceTask,
        fetched: FetchResult,
        request: CollectionRequest,
    ) -> SourceResult:
        if fetched.blocked:
            return SourceResult(
                observations=[],
                blocked=True,
                errors=[
                    StructuredError(
                        code="PASTVU_ACCESS_BLOCKED",
                        message="PastVu public API blocked the historical photo request",
                        retryable=True,
                        source_id=self.source_id,
                    )
                ],
            )
        if fetched.status_code < 200 or fetched.status_code >= 300:
            return SourceResult(
                observations=[],
                errors=[
                    StructuredError(
                        code="PASTVU_API_HTTP_ERROR",
                        message=f"PastVu public API returned HTTP {fetched.status_code}",
                        retryable=fetched.status_code >= 500 or fetched.status_code == 429,
                        source_id=self.source_id,
                    )
                ],
            )

        payload = self._payload(fetched.text)
        if payload is None:
            return SourceResult(
                observations=[],
                errors=[
                    StructuredError(
                        code="PASTVU_API_INVALID_RESPONSE",
                        message="PastVu public API returned an invalid JSON response",
                        retryable=True,
                        source_id=self.source_id,
                    )
                ],
            )
        photos = self._photos(payload)
        if photos is None:
            return SourceResult(
                observations=[],
                errors=[
                    StructuredError(
                        code="PASTVU_API_INVALID_RESPONSE",
                        message="PastVu public API response does not contain a photo array",
                        retryable=True,
                        source_id=self.source_id,
                    )
                ],
            )

        collection_id = str(task.metadata.get("collection_id") or "")
        snapshot = await self.snapshots.capture(
            self.source_id,
            fetched.final_url,
            fetched.text,
            fetched.content_type or "application/json",
            collection_id=collection_id,
        )
        observations: list[Observation] = []
        evidence_items: list[Evidence] = []
        for raw in photos[: self.max_items]:
            normalized = self._photo(raw)
            if normalized is None:
                continue
            observation, evidence = self._fact(
                normalized,
                request=request,
                collection_id=collection_id,
                snapshot_id=snapshot.snapshot_id,
                api_url=fetched.final_url,
                task=task,
            )
            observations.append(observation)
            evidence_items.append(evidence)

        return SourceResult(
            observations=observations,
            evidence=evidence_items,
            partial=len(photos) > self.max_items,
            errors=(
                [
                    StructuredError(
                        code="PASTVU_RESULT_LIMIT_REACHED",
                        message=(
                            "PastVu returned more photo records than the bounded extraction "
                            f"limit of {self.max_items}"
                        ),
                        retryable=False,
                        source_id=self.source_id,
                    )
                ]
                if len(photos) > self.max_items
                else []
            ),
        )

    async def normalize(self, result: SourceResult) -> SourceResult:
        return result

    async def health(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "status": "ready",
            "api_endpoint": self.api_endpoint,
            "api_method": self.api_method,
            "historical_images": True,
            "source_declared_only": True,
            "model_output_is_evidence": False,
            "max_items": self.max_items,
            "default_distance_meters": self.default_distance_meters,
            "geocoding": (
                await self.geocoder.health()
                if self.geocoder is not None
                else {"status": "not_configured"}
            ),
        }

    async def _resolve_point(
        self,
        request: CollectionRequest,
    ) -> tuple[Point | None, dict[str, object] | None]:
        if self.geocoder is None:
            return None, None
        query = self._territory_query(request)
        if not query:
            return None, None
        result = await self.geocoder.search(
            query,
            limit=1,
            language=request.constraints.language,
        )
        if result.blocked or result.errors or not result.candidates:
            return None, None
        candidate = result.candidates[0]
        return candidate.point, {
            "provider": candidate.provider,
            "provider_place_id": candidate.provider_place_id,
            "display_name": candidate.display_name,
            "point": candidate.point.model_dump(mode="json"),
            "source_url": candidate.source_url,
            "importance": candidate.importance,
            "provenance": candidate.provenance,
            "navigation_only": True,
            "geocoding_is_historical_evidence": False,
        }

    @classmethod
    def _distance(cls, request: CollectionRequest) -> int:
        requested = request.territory.radius_meters or cls.default_distance_meters
        return max(100, min(int(requested), cls.max_distance_meters))

    @staticmethod
    def _territory_query(request: CollectionRequest) -> str:
        city = (request.territory.city or "").strip()
        address = (request.territory.address or "").strip()
        if city and address:
            return address if city.casefold() in address.casefold() else f"{city}, {address}"
        return address or city

    @staticmethod
    def _payload(text: str) -> dict[str, Any] | None:
        try:
            value = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    @staticmethod
    def _photos(payload: dict[str, Any]) -> list[Any] | None:
        result = payload.get("result")
        if not isinstance(result, dict):
            return None
        photos = result.get("photos")
        return photos if isinstance(photos, list) else None

    @classmethod
    def _photo(cls, raw: Any) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            return None
        try:
            cid = int(raw.get("cid"))
        except (TypeError, ValueError):
            return None
        if cid <= 0:
            return None
        file_value = str(raw.get("file") or "").strip().lstrip("/")
        if not file_value or len(file_value) > 2_000:
            return None
        year = cls._optional_year(raw.get("year"))
        year2 = cls._optional_year(raw.get("year2"))
        title = cls._text(raw.get("title"), 2_000)
        geo = cls._point(raw.get("geo"))
        distance = cls._optional_float(raw.get("distance"))
        return {
            "cid": cid,
            "title": title,
            "year": year,
            "year2": year2,
            "geo": geo,
            "distance": distance,
            "file": file_value,
            "page_url": f"{cls.item_origin}/p/{cid}",
            "image_url": f"{cls.image_origin}/d/{file_value}",
            "thumbnail_url": f"{cls.image_origin}/h/{file_value}",
        }

    def _fact(
        self,
        photo: dict[str, Any],
        *,
        request: CollectionRequest,
        collection_id: str,
        snapshot_id: str,
        api_url: str,
        task: SourceTask,
    ) -> tuple[Observation, Evidence]:
        geo = photo["geo"] if isinstance(photo.get("geo"), Point) else None
        facts = {
            "cid": photo["cid"],
            "title": photo["title"],
            "year": photo["year"],
            "year2": photo["year2"],
            "geo": geo.model_dump(mode="json") if geo is not None else None,
            "distance": photo["distance"],
            "image_url": photo["image_url"],
            "thumbnail_url": photo["thumbnail_url"],
            "page_url": photo["page_url"],
        }
        canonical = json.dumps(
            facts,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        content_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        entity_id = f"pastvu:{photo['cid']}"
        observation_id = stable_observation_id(
            collection_id=collection_id,
            source_id=self.source_id,
            entity_type="image",
            entity_id=entity_id,
            source_url=str(photo["page_url"]),
            content_hash=content_hash,
        )
        provenance: dict[str, object] = {
            "snapshot_id": snapshot_id,
            "extractor": self.extractor_version,
            "api": {
                "url": api_url,
                "method": self.api_method,
                "source_declared": True,
            },
            "historical_image": True,
            "source_declared_image": True,
            "model_output_is_evidence": False,
        }
        geocoding = task.metadata.get("geocoding")
        if isinstance(geocoding, dict):
            provenance["geocoding"] = geocoding
        observation = Observation(
            observation_id=observation_id,
            collection_id=collection_id,
            analysis_id=request.analysis_id,
            consumer=request.consumer,
            source=self.source_id,
            source_kind="historical_image_reference",
            url=str(photo["page_url"]),
            entity_type="image",
            entity_id=entity_id,
            title=photo["title"],
            text=self._display_text(photo),
            data={
                **facts,
                "historical_image": True,
                "image_binary_retrieved": False,
            },
            geo=geo,
            content_hash=content_hash,
            provenance=provenance,
            quality={
                "evidence_backed": True,
                "source_declared_image": True,
                "historical_image": True,
                "image_binary_retrieved": False,
                "intent_evidence": {"historical_images": True},
            },
        )
        evidence = Evidence(
            evidence_id=stable_evidence_id(
                observation_id=observation_id,
                evidence_type="historical_image_reference",
                source_url=str(photo["page_url"]),
                text=canonical,
            ),
            observation_id=observation_id,
            type="historical_image_reference",
            text=canonical,
            source=EvidenceSource(
                provider=self.source_id,
                url=str(photo["page_url"]),
                collected_at=observation.collected_at,
                source_id=self.source_id,
            ),
            metadata={
                "intent": "historical_images",
                "source_declared": True,
                "historical_image": True,
                "snapshot_id": snapshot_id,
                "api_url": api_url,
            },
        )
        return observation, evidence

    @staticmethod
    def _display_text(photo: dict[str, Any]) -> str:
        rows: list[str] = []
        title = photo.get("title")
        if isinstance(title, str) and title:
            rows.append(title)
        year = photo.get("year")
        year2 = photo.get("year2")
        if isinstance(year, int):
            rows.append(str(year) if year2 in {None, year} else f"{year}–{year2}")
        rows.append(str(photo["page_url"]))
        return "\n".join(rows)[:10_000]

    @staticmethod
    def _point(value: Any) -> Point | None:
        if not isinstance(value, (list, tuple)) or len(value) < 2:
            return None
        try:
            return Point(latitude=float(value[0]), longitude=float(value[1]))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _optional_year(value: Any) -> int | None:
        try:
            year = int(value)
        except (TypeError, ValueError):
            return None
        return year if 1_000 <= year <= 3_000 else None

    @staticmethod
    def _optional_float(value: Any) -> float | None:
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _text(value: Any, limit: int) -> str | None:
        if value is None:
            return None
        text = " ".join(str(value).split()).strip()
        return text[:limit] if text else None
