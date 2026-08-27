from __future__ import annotations

from urllib.parse import urlparse

from argus.contracts.models import CollectionRequest
from argus.research.discovery import DiscoveryOutcome
from argus.research.discovery_relevance import TerritoryAwareDiscoveryService
from argus.research.input_candidates import research_input_candidates
from argus.research.residential_sources import RESIDENTIAL_INTENTS


class DedicatedSourceRoutingDiscoveryService(TerritoryAwareDiscoveryService):
    """Route discovered public URLs to dedicated adapters by verified hostname.

    Discovery remains navigation only. This layer changes only which SourceAdapter will
    fetch a destination; it never treats a known domain as Evidence. Domain routing is
    consumer-neutral and can be extended for future public-source adapters without adding
    source-specific conditions to the orchestrator.

    Requests containing only source-scoped residential intents are additionally fail-closed:
    non-routed search destinations are discarded instead of becoming fallback factual
    sources. This keeps residential facts on the explicitly configured public source.
    """

    routing_version = "dedicated-source-routing/3"

    def __init__(self, *args, domain_source_routes: dict[str, str] | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.domain_source_routes = {
            self._normalize_domain(domain): str(source_id).strip()
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
            if source_id == "mingkh_residential":
                self._scope_mingkh_navigation_inputs(task, request)

        if self._residential_only(request):
            kept = [task for task in outcome.tasks if task.source_id == "mingkh_residential"]
            removed = len(outcome.tasks) - len(kept)
            outcome.tasks = kept
            outcome.destinations_selected = len(kept)
            if removed:
                outcome.destinations_skipped_budget += removed
            if not kept and outcome.stop_reason not in {"blocked_without_destinations", "no_queries"}:
                outcome.stop_reason = "source_policy_no_valid_destinations"
        return outcome

    @staticmethod
    def _scope_mingkh_navigation_inputs(task, request: CollectionRequest) -> None:
        """Keep public form values separate from discovery/search query strings.

        Search-provider queries are useful for locating a ``dom.mingkh.ru`` destination,
        but they are not valid values for the site's address/search controls. The dedicated
        residential route therefore exposes only bounded values derived from TerritoryContext
        to AGENT/SiteRecipe navigation.
        """

        task.metadata["research_input_candidates"] = research_input_candidates(request)
        task.metadata["research_input_candidates_navigation_only"] = True
        task.metadata["research_input_candidates_are_evidence"] = False
        task.metadata["research_input_scope"] = "territory_context"
        task.metadata["allowed_domains"] = ["dom.mingkh.ru"]

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
    def _residential_only(request: CollectionRequest) -> bool:
        requested = {str(item).strip() for item in request.intents if str(item).strip()}
        return bool(requested) and requested.issubset(RESIDENTIAL_INTENTS)

    @staticmethod
    def _normalize_domain(value: str) -> str:
        return str(value).strip().casefold().strip(".")
