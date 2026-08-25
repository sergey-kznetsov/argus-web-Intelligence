from __future__ import annotations

from argus.contracts.models import CollectionRequest
from argus.research.url_identity import canonicalize_discovery_url
from argus.sources.base import SourceTask
from argus.sources.kmz_web import KmzAwareWebAdapter


class CanonicalLinkWebAdapter(KmzAwareWebAdapter):
    """Apply the shared discovery URL identity to seed and in-page navigation tasks.

    Explicit task keys are preserved untouched because they represent provider
    operations rather than ordinary URL-only GET identity.
    """

    navigation_identity_version = "discovery-url-identity/1"

    async def discover(self, request: CollectionRequest) -> list[SourceTask]:
        return self._canonicalize_navigation_tasks(await super().discover(request))

    def _discovered_tasks(
        self,
        task: SourceTask,
        fetched,
        request: CollectionRequest,
        collection_id: str,
    ) -> list[SourceTask]:
        tasks = super()._discovered_tasks(task, fetched, request, collection_id)
        return self._canonicalize_navigation_tasks(tasks)

    def _canonicalize_navigation_tasks(self, tasks: list[SourceTask]) -> list[SourceTask]:
        normalized: list[SourceTask] = []
        seen: set[str] = set()
        for task in tasks:
            if task.task_key is not None:
                key = task.dedupe_key
                if key not in seen:
                    seen.add(key)
                    normalized.append(task)
                continue

            canonical_url = canonicalize_discovery_url(task.url)
            if canonical_url is None:
                continue
            key = f"{task.source_id}:{canonical_url}"
            if key in seen:
                continue
            seen.add(key)
            metadata = dict(task.metadata)
            metadata["navigation_original_url"] = task.url
            metadata["navigation_canonical_url"] = canonical_url
            metadata["navigation_identity_version"] = self.navigation_identity_version
            normalized.append(
                SourceTask(
                    source_id=task.source_id,
                    goal=task.goal,
                    url=canonical_url,
                    depth=task.depth,
                    metadata=metadata,
                )
            )
        return normalized

    async def health(self) -> dict[str, object]:
        payload = dict(await super().health())
        payload["canonical_navigation_identity"] = self.navigation_identity_version
        return payload
