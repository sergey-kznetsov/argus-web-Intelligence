from __future__ import annotations

from argus.orchestrator.atomic import AtomicCollectionOrchestrator


class DuplicateAwareAtomicCollectionOrchestrator(AtomicCollectionOrchestrator):
    """Atomic orchestrator that does not branch research from duplicate documents."""

    async def _expand_historical(
        self,
        record,
        task,
        observations,
        pending,
        visited,
        seen_queries,
    ) -> None:
        if bool(task.metadata.get("duplicate_content")):
            return
        await super()._expand_historical(
            record,
            task,
            observations,
            pending,
            visited,
            seen_queries,
        )
