from __future__ import annotations

from argus.contracts.models import CollectionRequest, StructuredError
from argus.history.timeline import HistoricalTimelineBuilder
from argus.sources.base import SourceResult, SourceTask
from argus.sources.image_web import ImageAwareRecipeWebAdapter


class HistoricalTimelineWebAdapter(ImageAwareRecipeWebAdapter):
    """Attach archive identity and derive bounded changes across committed captures."""

    def __init__(self, *args, historical_timeline=None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.historical_timeline = historical_timeline or HistoricalTimelineBuilder()

    async def extract(
        self,
        task: SourceTask,
        fetched,
        request: CollectionRequest,
    ) -> SourceResult:
        result = await super().extract(task, fetched, request)
        original_url = str(task.metadata.get("archive_original_url") or "").strip()
        capture_timestamp = str(task.metadata.get("archive_timestamp") or "").strip()
        if not original_url or not self._valid_timestamp(capture_timestamp):
            return result

        collection_id = str(task.metadata.get("collection_id") or "").strip()
        archive = {
            "provider": str(task.metadata.get("discovery_provider") or "wayback_cdx"),
            "original_url": original_url,
            "capture_url": str(fetched.final_url),
            "capture_timestamp": capture_timestamp,
            "historical_capture": True,
            "network_link_followed": False,
        }

        raw_observation_ids = {item.observation_id for item in result.observations}
        for observation in result.observations:
            observation.provenance["archive"] = dict(archive)
            observation.data["archive"] = dict(archive)
            observation.quality["historical_capture"] = True
        for evidence in result.evidence:
            if evidence.observation_id in raw_observation_ids:
                evidence.metadata["archive"] = dict(archive)

        # Archived documents are exact historical versions. Do not follow ordinary
        # in-page/archive-rewritten links; research expansion is handled separately by
        # the bounded historical branch planner from the extracted facts.
        result.discovered_tasks = []
        task.metadata["historical_capture"] = True

        committed = (
            await self.repository.list_observations(collection_id)
            if collection_id
            else []
        )
        previous_timestamp = self._previous_capture_timestamp(
            committed,
            original_url=original_url,
            current_timestamp=capture_timestamp,
        )
        previous = [
            item
            for item in committed
            if self._archive_value(item, "original_url") == original_url
            and self._archive_value(item, "capture_timestamp") == previous_timestamp
        ] if previous_timestamp else []

        derivation = self.historical_timeline.derive(
            current=list(result.observations),
            previous=previous,
            request=request,
            original_url=original_url,
            capture_url=str(fetched.final_url),
            capture_timestamp=capture_timestamp,
            previous_capture_timestamp=previous_timestamp,
        )
        result.observations.extend(derivation.observations)
        result.evidence.extend(derivation.evidence)
        if derivation.truncated:
            result.partial = True
            result.errors.append(
                StructuredError(
                    code="HISTORICAL_CHANGE_BUDGET_EXHAUSTED",
                    message=(
                        "Historical entity changes exceeded the bounded comparison budget; "
                        f"observed={derivation.changes_seen}, "
                        f"emitted={self.historical_timeline.max_entity_changes}."
                    ),
                    retryable=False,
                    source_id=self.source_id,
                )
            )
        return result

    @staticmethod
    def _valid_timestamp(value: str) -> bool:
        return len(value) == 14 and value.isdigit()

    @classmethod
    def _previous_capture_timestamp(
        cls,
        observations,
        *,
        original_url: str,
        current_timestamp: str,
    ) -> str | None:
        candidates = {
            timestamp
            for item in observations
            if cls._archive_value(item, "original_url") == original_url
            for timestamp in [cls._archive_value(item, "capture_timestamp")]
            if timestamp and cls._valid_timestamp(timestamp) and timestamp < current_timestamp
        }
        return max(candidates) if candidates else None

    @staticmethod
    def _archive_value(observation, key: str) -> str | None:
        archive = observation.provenance.get("archive")
        if not isinstance(archive, dict):
            return None
        value = archive.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    async def health(self) -> dict[str, object]:
        payload = dict(await super().health())
        payload["historical_timeline"] = {
            "version": self.historical_timeline.version,
            "page_versions": True,
            "entity_appeared_disappeared": True,
            "tracked_fields": list(self.historical_timeline.tracked_data_fields),
            "max_entity_changes": self.historical_timeline.max_entity_changes,
            "max_diff_chars": self.historical_timeline.max_diff_chars,
            "archive_in_page_navigation": False,
            "semantic_inference": False,
            "image_references": True,
        }
        return payload
