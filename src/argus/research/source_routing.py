from __future__ import annotations

from urllib.parse import urlparse

from argus.contracts.models import CollectionRequest
from argus.research.discovery import DiscoveryOutcome
from argus.research.discovery_relevance import TerritoryAwareDiscoveryService


class DedicatedSourceRoutingDiscoveryService(TerritoryAwareDiscoveryService):
    """Route discovered public URLs to dedicated adapters by verified hostname.

    Discovery remains navigation only. This layer changes only which SourceAdapter will
    fetch a destination; it never treats a known domain as Evidence. Domain routing is
    consumer-neutral and can be extended for future public-source adapters without adding
    source-specific conditions to the orchestrator.
    """

    routing_version = "dedicated-source-routing/1"

    def __init__(self, *args, domain_source_routes: dict[str, str] | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.domain_source_routes = {
            self._normalize_domain(domain): source_id
            for domain, source_id in (domain_source_routes or {}).items()
            if self._normalize_domain(domain) and str(source_id).strip()
        }

    async def discover(
        self,
        queries: list[str],
        request: CollectionRequest,
    ) -> DiscoveryOutcome:
        outcome = await super().discover(queries, request)
        for task in outcome.tasks:
            if task.source_id != "generic_web":
                continue
            source_id = self._source_for_url(task.url)
            if source_id is None:
                continue
            task.source_id = source_id
            task.metadata["dedicated_source_route"] = {
                "source_id": source_id,
                "version": self.routing_version,
                "navigation_only": True,
                "is_evidence": False,
            }
        return outcome

    def _source_for_url(self, url: str) -> str | None:
        host = self._normalize_domain(urlparse(url).hostname or "")
        if not host:
            return None
        matches = [
            (domain, source_id)
            for domain, source_id in self.domain_source_routes.items()
            if host == domain or host.endswith(f".{domain}")
        ]
        if not matches:
            return None
        matches.sort(key=lambda item: len(item[0]), reverse=True)
        return matches[0][1]

    @staticmethod
    def _normalize_domain(value: str) -> str:
        return str(value).strip().casefold().strip(".")
