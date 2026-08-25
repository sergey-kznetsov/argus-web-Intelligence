from __future__ import annotations

from argus.contracts.models import CollectionRequest
from argus.normalization.public_map_provenance import classify_public_map_url
from argus.sources.base import SourceResult, SourceTask
from argus.sources.historical_web import HistoricalTimelineWebAdapter


class PublicMapProvenanceWebAdapter(HistoricalTimelineWebAdapter):
    """Attach provider provenance to facts originating from known public map web pages."""

    async def extract(
        self,
        task: SourceTask,
        fetched,
        request: CollectionRequest,
    ) -> SourceResult:
        result = await super().extract(task, fetched, request)

        for observation in result.observations:
            provenance = classify_public_map_url(observation.url)
            if provenance is None:
                continue
            observation.provenance["public_map_source"] = dict(provenance)
            observation.quality["public_map_source_identified"] = True

        for evidence in result.evidence:
            provenance = classify_public_map_url(evidence.source.url)
            if provenance is None:
                continue
            evidence.metadata["public_map_source"] = dict(provenance)
        return result

    async def health(self) -> dict[str, object]:
        payload = dict(await super().health())
        payload["public_map_web_provenance"] = {
            "enabled": True,
            "providers": ["yandex_maps_web", "2gis_web", "google_maps_web"],
            "classification_basis": "url_host_path",
            "content_inference": False,
            "paid_api": False,
        }
        return payload
