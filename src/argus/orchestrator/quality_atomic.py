from __future__ import annotations

from argus.normalization.provenance_quality import ProvenanceQualityNormalizer
from argus.orchestrator.duplicate_atomic import DuplicateAwareAtomicCollectionOrchestrator


class QualityAwareAtomicCollectionOrchestrator(DuplicateAwareAtomicCollectionOrchestrator):
    """Apply uniform provenance/quality facts before one atomic source-task commit."""

    def __init__(self, *args, provenance_quality=None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.provenance_quality = provenance_quality or ProvenanceQualityNormalizer()

    async def _commit_task_success(
        self,
        record,
        *,
        observations,
        evidence,
        snapshots,
    ) -> None:
        self.provenance_quality.normalize(observations, evidence, snapshots)
        await super()._commit_task_success(
            record,
            observations=observations,
            evidence=evidence,
            snapshots=snapshots,
        )
