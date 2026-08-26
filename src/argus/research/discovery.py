from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Protocol
from urllib.parse import unquote, urlparse

from argus.contracts.models import CollectionRequest, StructuredError
from argus.research.url_identity import canonicalize_discovery_url
from argus.security.redaction import safe_error_message
from argus.security.urls import UnsafeUrlError, UrlGuard
from argus.sources.base import SourceTask


class DiscoveryBlockedError(RuntimeError):
    """A discovery provider presented an anti-bot/access challenge.

    ARGUS never attempts to bypass the challenge. The orchestrator can expose the
    collection as blocked/partial instead of misreporting a normal source failure.
    """


@dataclass(slots=True)
class DiscoveryHit:
    url: str
    provider: str
    title: str | None = None
    engines: list[str] = field(default_factory=list)
    rank: int | None = None
    query: str | None = None


class DiscoveryProvider(Protocol):
    name: str

    async def discover(
        self,
        queries: list[str],
        request: CollectionRequest,
    ) -> list[DiscoveryHit]: ...

    async def health(self) -> dict[str, object]: ...


@dataclass(slots=True)
class DiscoveryOutcome:
    tasks: list[SourceTask] = field(default_factory=list)
    errors: list[StructuredError] = field(default_factory=list)
    providers_attempted: list[str] = field(default_factory=list)
    blocked: bool = False
    candidates_seen: int = 0
    valid_destinations: int = 0
    destinations_selected: int = 0
    destinations_skipped_budget: int = 0
    archive_companions_skipped_budget: int = 0
    task_budget: int = 0
    stop_reason: str | None = None


@dataclass(slots=True)
class _PreparedHit:
    hit: DiscoveryHit
    canonical_url: str
    domain_priority: int
    locality_matches: int
    https: bool
    navigation_score: int


class DiscoveryService:
    """Turn research queries into bounded, ranked factual-source crawl tasks.

    Discovery hits are navigation only, never Evidence. Providers remain ordered
    fallbacks: ARGUS uses the first provider that yields valid destinations and does
    not generate extra search traffic once factual crawl candidates exist.
    """

    ranking_version = "discovery-ranking/1"
    telemetry_version = "discovery-telemetry/2"
    stop_policy = "first_provider_with_valid_destinations"

    def __init__(
        self,
        providers: list[DiscoveryProvider],
        url_guard: UrlGuard,
        max_queries: int = 8,
        historical_archive_source_id: str | None = None,
    ) -> None:
        self.providers = providers
        self.url_guard = url_guard
        self.max_queries = max_queries
        self.historical_archive_source_id = historical_archive_source_id

    async def discover(
        self,
        queries: list[str],
        request: CollectionRequest,
    ) -> DiscoveryOutcome:
        outcome = DiscoveryOutcome()
        selected_queries = [query for query in queries if query.strip()][: self.max_queries]
        if not selected_queries:
            outcome.stop_reason = "no_queries"
            return outcome

        allowed_order = self._normalized_domains(request.constraints.allowed_domains)
        allowed = set(allowed_order)
        denied = set(self._normalized_domains(request.constraints.denied_domains))
        locality_tokens = self._locality_tokens(request)
        seen: set[str] = set()
        task_budget = max(1, int(request.constraints.max_pages))
        outcome.task_budget = task_budget

        for provider in self.providers:
            outcome.providers_attempted.append(provider.name)
            try:
                hits = await provider.discover(selected_queries, request)
            except DiscoveryBlockedError as exc:
                outcome.blocked = True
                outcome.errors.append(
                    StructuredError(
                        code="DISCOVERY_BLOCKED",
                        message=safe_error_message(exc, max_length=300),
                        retryable=True,
                        source_id=f"discovery:{provider.name}",
                    )
                )
                continue
            except Exception as exc:
                outcome.errors.append(
                    StructuredError(
                        code="DISCOVERY_ERROR",
                        message=safe_error_message(exc, max_length=300),
                        retryable=True,
                        source_id=f"discovery:{provider.name}",
                    )
                )
                continue

            outcome.candidates_seen += len(hits)
            prepared = await self._prepare_hits(
                hits,
                allowed=allowed,
                denied=denied,
                allowed_order=allowed_order,
                locality_tokens=locality_tokens,
                seen=seen,
            )
            outcome.valid_destinations += len(prepared)
            prepared.sort(key=self._ranking_key)

            destinations_added = 0
            for index, candidate in enumerate(prepared):
                if len(outcome.tasks) >= task_budget:
                    outcome.destinations_skipped_budget += len(prepared) - index
                    outcome.stop_reason = "task_budget_reached"
                    break
                hit = candidate.hit
                canonical_url = candidate.canonical_url
                seen.add(canonical_url)
                ranking_components = {
                    "domain_priority": candidate.domain_priority,
                    "provider_rank": hit.rank,
                    "locality_matches": candidate.locality_matches,
                    "https": candidate.https,
                }
                common_metadata = {
                    "discovery_provider": hit.provider,
                    "discovery_engines": hit.engines,
                    "discovery_rank": hit.rank,
                    "discovery_query": hit.query,
                    "discovery_original_url": hit.url,
                    "discovery_canonical_url": canonical_url,
                    "discovery_domain_priority": candidate.domain_priority,
                    "discovery_locality_matches": candidate.locality_matches,
                    "discovery_https": candidate.https,
                    "discovery_navigation_score": candidate.navigation_score,
                    "discovery_ranking_components": ranking_components,
                    "discovery_ranking_version": self.ranking_version,
                    "discovery_telemetry_version": self.telemetry_version,
                    "discovery_stop_policy": self.stop_policy,
                    "discovery_task_budget": task_budget,
                    "allowed_domains": list(request.constraints.allowed_domains),
                    "research_goals": list(request.intents),
                }
                outcome.tasks.append(
                    SourceTask(
                        source_id="generic_web",
                        goal=request.intents[0],
                        url=canonical_url,
                        depth=0,
                        metadata=dict(common_metadata),
                    )
                )
                destinations_added += 1
                outcome.destinations_selected += 1

                archive_requested = bool(
                    self.historical_archive_source_id
                    and "historical_context" in request.intents
                )
                if archive_requested:
                    if len(outcome.tasks) < task_budget:
                        outcome.tasks.append(
                            SourceTask(
                                source_id=str(self.historical_archive_source_id),
                                goal="historical_context",
                                url=canonical_url,
                                depth=0,
                                task_key=f"{self.historical_archive_source_id}:{canonical_url}",
                                metadata={
                                    **common_metadata,
                                    "archive_target_url": canonical_url,
                                },
                            )
                        )
                    else:
                        outcome.archive_companions_skipped_budget += 1
                        outcome.stop_reason = "task_budget_reached"

            if destinations_added:
                if outcome.stop_reason is None:
                    outcome.stop_reason = self.stop_policy
                break

        if not outcome.tasks and outcome.providers_attempted and outcome.blocked:
            outcome.stop_reason = "blocked_without_destinations"
            outcome.errors.append(
                StructuredError(
                    code="DISCOVERY_INCOMPLETE",
                    message=(
                        "Discovery was blocked before any valid destination URL was found. "
                        "ARGUS did not attempt to bypass the access challenge."
                    ),
                    retryable=True,
                    source_id="discovery",
                )
            )
        elif not outcome.tasks and outcome.providers_attempted and not outcome.errors:
            outcome.stop_reason = "no_valid_destinations"
            outcome.errors.append(
                StructuredError(
                    code="DISCOVERY_NO_RESULTS",
                    message=(
                        "Discovery providers returned no valid destination URLs "
                        "for the research queries."
                    ),
                    retryable=False,
                    source_id="discovery",
                )
            )
        elif not outcome.tasks and outcome.stop_reason is None:
            outcome.stop_reason = "providers_exhausted"
        return outcome

    async def _prepare_hits(
        self,
        hits: list[DiscoveryHit],
        *,
        allowed: set[str],
        denied: set[str],
        allowed_order: list[str],
        locality_tokens: tuple[str, ...],
        seen: set[str],
    ) -> list[_PreparedHit]:
        prepared: list[_PreparedHit] = []
        batch_seen: set[str] = set()
        for hit in hits:
            canonical_url = canonicalize_discovery_url(hit.url)
            if canonical_url is None or canonical_url in seen or canonical_url in batch_seen:
                continue
            try:
                await self.url_guard.validate(canonical_url)
            except UnsafeUrlError:
                continue
            host = (urlparse(canonical_url).hostname or "").lower().strip(".")
            if not host:
                continue
            if denied and any(self._host_matches(host, domain) for domain in denied):
                continue
            domain_priority = self._domain_priority(host, allowed_order)
            if allowed and domain_priority >= len(allowed_order):
                continue
            locality_matches = self._locality_match_count(hit, canonical_url, locality_tokens)
            https = canonical_url.startswith("https://")
            navigation_score = self._navigation_score(
                domain_priority=domain_priority,
                provider_rank=hit.rank,
                locality_matches=locality_matches,
                https=https,
            )
            batch_seen.add(canonical_url)
            prepared.append(
                _PreparedHit(
                    hit=hit,
                    canonical_url=canonical_url,
                    domain_priority=domain_priority,
                    locality_matches=locality_matches,
                    https=https,
                    navigation_score=navigation_score,
                )
            )
        return prepared

    @staticmethod
    def _ranking_key(candidate: _PreparedHit) -> tuple[int, int, int, int, str]:
        provider_rank = candidate.hit.rank if candidate.hit.rank is not None else 1_000_000
        return (
            candidate.domain_priority,
            provider_rank,
            -candidate.locality_matches,
            0 if candidate.https else 1,
            candidate.canonical_url,
        )

    @staticmethod
    def _navigation_score(
        *,
        domain_priority: int,
        provider_rank: int | None,
        locality_matches: int,
        https: bool,
    ) -> int:
        score = 100
        score -= min(domain_priority, 20) * 5
        score -= min(max((provider_rank or 1) - 1, 0), 50)
        score += min(locality_matches, 5) * 4
        score += 2 if https else 0
        return max(0, min(100, score))

    @classmethod
    def _domain_priority(cls, host: str, allowed_order: list[str]) -> int:
        if not allowed_order:
            return 0
        for index, domain in enumerate(allowed_order):
            if cls._host_matches(host, domain):
                return index
        return len(allowed_order)

    @classmethod
    def _locality_match_count(
        cls,
        hit: DiscoveryHit,
        canonical_url: str,
        locality_tokens: tuple[str, ...],
    ) -> int:
        if not locality_tokens:
            return 0
        haystack = " ".join((hit.title or "", unquote(canonical_url))).casefold()
        return sum(1 for token in locality_tokens if token in haystack)

    @staticmethod
    def _locality_tokens(request: CollectionRequest) -> tuple[str, ...]:
        values: list[str] = []
        for raw in (request.territory.city, request.territory.address):
            if not raw:
                continue
            values.extend(re.findall(r"[\w\-]{3,}", raw.casefold(), flags=re.UNICODE))
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            if value in seen:
                continue
            seen.add(value)
            result.append(value)
        return tuple(result[:12])

    @staticmethod
    def _normalized_domains(values: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            domain = value.lower().strip().strip(".")
            if not domain or domain in seen:
                continue
            seen.add(domain)
            result.append(domain)
        return result

    @staticmethod
    def _host_matches(host: str, domain: str) -> bool:
        return host == domain or host.endswith(f".{domain}")
