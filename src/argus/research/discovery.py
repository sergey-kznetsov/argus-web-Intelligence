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
    telemetry_version = "discovery-telemetry/1"
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
        local_seen: set[str] = set()
        for hit in hits:
            canonical_url = canonicalize_discovery_url(hit.url)
            if canonical_url is None or canonical_url in seen or canonical_url in local_seen:
                continue
            if not self._domain_allowed(canonical_url, allowed, denied):
                continue
            try:
                await self.url_guard.validate(canonical_url)
            except UnsafeUrlError:
                continue
            local_seen.add(canonical_url)
            domain_priority = self._domain_priority(canonical_url, allowed_order)
            locality_matches = self._locality_matches(hit, canonical_url, locality_tokens)
            https = canonical_url.casefold().startswith("https://")
            prepared.append(
                _PreparedHit(
                    hit=hit,
                    canonical_url=canonical_url,
                    domain_priority=domain_priority,
                    locality_matches=locality_matches,
                    https=https,
                    navigation_score=self._navigation_score(
                        hit,
                        domain_priority=domain_priority,
                        allowed_count=len(allowed_order),
                        locality_matches=locality_matches,
                        https=https,
                    ),
                )
            )
        return prepared

    @staticmethod
    def _ranking_key(hit: _PreparedHit) -> tuple[int, int, int, int, str]:
        rank = hit.hit.rank if hit.hit.rank is not None and hit.hit.rank >= 0 else 1_000_000_000
        return (
            hit.domain_priority,
            rank,
            -hit.locality_matches,
            -int(hit.https),
            hit.canonical_url,
        )

    @staticmethod
    def _navigation_score(
        hit: DiscoveryHit,
        *,
        domain_priority: int,
        allowed_count: int,
        locality_matches: int,
        https: bool,
    ) -> int:
        """Return an explainable navigation score; never an Evidence confidence score."""
        score = 0
        if allowed_count and domain_priority < allowed_count:
            score += max(10, 30 - (domain_priority * 5))
        if hit.rank is not None and hit.rank >= 1:
            score += max(0, 40 - min(hit.rank - 1, 40))
        score += min(25, max(0, locality_matches) * 10)
        if https:
            score += 5
        return min(100, score)

    @staticmethod
    def _normalized_domains(values: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            domain = value.casefold().strip().strip(".")
            if not domain or domain in seen:
                continue
            seen.add(domain)
            result.append(domain)
        return result

    @staticmethod
    def _domain_allowed(url: str, allowed: set[str], denied: set[str]) -> bool:
        domain = (urlparse(url).hostname or "").casefold().strip(".")
        if not domain:
            return False
        if any(domain == item or domain.endswith("." + item) for item in denied):
            return False
        if allowed:
            return any(domain == item or domain.endswith("." + item) for item in allowed)
        return True

    @staticmethod
    def _domain_priority(url: str, allowed_order: list[str]) -> int:
        if not allowed_order:
            return 0
        domain = (urlparse(url).hostname or "").casefold().strip(".")
        for index, candidate in enumerate(allowed_order):
            if domain == candidate or domain.endswith("." + candidate):
                return index
        return len(allowed_order)

    @staticmethod
    def _locality_tokens(request: CollectionRequest) -> tuple[str, ...]:
        source = " ".join(
            value.strip()
            for value in (request.territory.city or "", request.territory.address or "")
            if value.strip()
        ).casefold()
        tokens: list[str] = []
        seen: set[str] = set()
        for token in re.findall(r"[\w-]+", source, flags=re.UNICODE):
            if len(token) < 3 or token in seen:
                continue
            seen.add(token)
            tokens.append(token)
        return tuple(tokens)

    @staticmethod
    def _locality_matches(
        hit: DiscoveryHit,
        canonical_url: str,
        tokens: tuple[str, ...],
    ) -> int:
        if not tokens:
            return 0
        haystack = f"{hit.title or ''} {unquote(canonical_url)}".casefold()
        return sum(1 for token in tokens if token in haystack)
