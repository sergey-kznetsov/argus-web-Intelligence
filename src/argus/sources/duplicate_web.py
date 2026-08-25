from __future__ import annotations

from argus.contracts.models import CollectionRequest, Observation
from argus.sources.base import SourceResult, SourceTask
from argus.sources.canonical_web import CanonicalLinkWebAdapter
from argus.storage.base import Repository


class DuplicateAwareWebAdapter(CanonicalLinkWebAdapter):
    """Suppress recursive expansion of already committed document content.

    The duplicate itself remains a normal Observation/Evidence pair. Only navigation
    and historical expansion are suppressed. Lookup is collection-scoped and backed
    by committed storage, so worker restart/replay cannot inherit an uncommitted cache.
    """

    duplicate_identity_version = "committed-content-hash/1"
    min_web_text_chars = 256
    _PRIMARY_SOURCE_KINDS = {
        "web_page",
        "pdf_document",
        "structured_data",
        "office_document",
        "office_spreadsheet",
        "office_document_file",
    }
    _DISCOVERY_METADATA_KEYS = (
        "discovery_provider",
        "discovery_engines",
        "discovery_rank",
        "discovery_original_url",
        "discovery_canonical_url",
        "discovery_domain_priority",
        "discovery_locality_matches",
        "discovery_https",
        "discovery_navigation_score",
        "discovery_ranking_components",
        "discovery_ranking_version",
        "discovery_telemetry_version",
        "discovery_stop_policy",
        "discovery_task_budget",
    )

    def __init__(self, *args, repository: Repository, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.repository = repository

    async def extract(
        self,
        task: SourceTask,
        fetched,
        request: CollectionRequest,
    ) -> SourceResult:
        result = await super().extract(task, fetched, request)
        self._attach_discovery_navigation_provenance(result, task)
        if fetched.blocked or not result.observations:
            return result

        primary = self._primary_observation(result)
        if primary is None or not self._eligible_primary(primary):
            return result

        collection_id = primary.collection_id
        duplicate_of = await self.repository.find_observation_by_content_hash(
            collection_id,
            content_hash=primary.content_hash,
            source_kinds=[primary.source_kind],
        )
        if duplicate_of is None or duplicate_of.observation_id == primary.observation_id:
            return result

        self._mark_duplicate(result, primary, duplicate_of)
        result.discovered_tasks = []
        task.metadata["duplicate_content"] = True
        task.metadata["duplicate_of_observation_id"] = duplicate_of.observation_id
        task.metadata["duplicate_identity_version"] = self.duplicate_identity_version
        return result

    @classmethod
    def _primary_observation(cls, result: SourceResult) -> Observation | None:
        for observation in result.observations:
            if observation.source_kind in cls._PRIMARY_SOURCE_KINDS:
                return observation
        return None

    def _eligible_primary(self, observation: Observation) -> bool:
        if not observation.content_hash.strip():
            return False
        if observation.source_kind != "web_page":
            return True
        text = observation.text or ""
        return len(text.strip()) >= self.min_web_text_chars

    @classmethod
    def _attach_discovery_navigation_provenance(
        cls,
        result: SourceResult,
        task: SourceTask,
    ) -> None:
        if not task.metadata.get("discovery_provider"):
            return
        navigation = {
            key.removeprefix("discovery_"): task.metadata[key]
            for key in cls._DISCOVERY_METADATA_KEYS
            if key in task.metadata
        }
        navigation["navigation_only"] = True
        navigation["is_evidence"] = False
        for observation in result.observations:
            existing = observation.provenance.get("discovery")
            if isinstance(existing, dict):
                existing.update(navigation)
            else:
                observation.provenance["discovery"] = dict(navigation)
        for evidence in result.evidence:
            evidence.metadata["discovery_navigation"] = dict(navigation)

    def _mark_duplicate(
        self,
        result: SourceResult,
        primary: Observation,
        duplicate_of: Observation,
    ) -> None:
        duplicate_metadata = {
            "observation_id": duplicate_of.observation_id,
            "url": duplicate_of.url,
            "content_hash": duplicate_of.content_hash,
            "source_kind": duplicate_of.source_kind,
            "identity_version": self.duplicate_identity_version,
            "collection_scoped": True,
        }
        primary.provenance["duplicate_content"] = duplicate_metadata
        primary.quality["duplicate_content"] = True
        primary.quality["duplicate_of"] = duplicate_of.observation_id
        primary.data["duplicate_of_observation_id"] = duplicate_of.observation_id
        primary.data["duplicate_of_url"] = duplicate_of.url
        primary.data["duplicate_navigation_suppressed"] = True

        for evidence in result.evidence:
            if evidence.observation_id == primary.observation_id:
                evidence.metadata["duplicate_content"] = duplicate_metadata

    async def health(self) -> dict[str, object]:
        payload = dict(await super().health())
        payload["duplicate_content_identity"] = self.duplicate_identity_version
        payload["duplicate_content_collection_scoped"] = True
        payload["duplicate_content_evidence_preserved"] = True
        payload["duplicate_content_navigation_suppressed"] = True
        payload["duplicate_content_min_web_text_chars"] = self.min_web_text_chars
        payload["discovery_navigation_provenance"] = True
        return payload
