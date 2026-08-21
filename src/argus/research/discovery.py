from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlparse

from argus.contracts.models import CollectionRequest, StructuredError
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


class DiscoveryService:
    """Turn research queries into factual-source crawl tasks.

    Discovery hits are not evidence. Providers are ordered fallbacks: once one
    provider yields at least one valid destination URL, later providers are not
    called. This keeps search traffic small and avoids unnecessary anti-bot load.
    """

    def __init__(
        self,
        providers: list[DiscoveryProvider],
        url_guard: UrlGuard,
        max_queries: int = 8,
    ) -> None:
        self.providers = providers
        self.url_guard = url_guard
        self.max_queries = max_queries

    async def discover(
        self,
        queries: list[str],
        request: CollectionRequest,
    ) -> DiscoveryOutcome:
        outcome = DiscoveryOutcome()
        selected_queries = [query for query in queries if query.strip()][: self.max_queries]
        if not selected_queries:
            return outcome

        allowed = {domain.lower().strip(".") for domain in request.constraints.allowed_domains}
        denied = {domain.lower().strip(".") for domain in request.constraints.denied_domains}
        seen: set[str] = set()

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

            before = len(outcome.tasks)
            for hit in hits:
                if hit.url in seen or not self._domain_allowed(hit.url, allowed, denied):
                    continue
                try:
                    await self.url_guard.validate(hit.url)
                except UnsafeUrlError:
                    continue
                seen.add(hit.url)
                outcome.tasks.append(
                    SourceTask(
                        source_id="generic_web",
                        goal=request.intents[0],
                        url=hit.url,
                        depth=0,
                        metadata={
                            "discovery_provider": hit.provider,
                            "discovery_engines": hit.engines,
                            "discovery_rank": hit.rank,
                            "allowed_domains": list(request.constraints.allowed_domains),
                        },
                    )
                )
            if len(outcome.tasks) > before:
                break

        if (
            not outcome.tasks
            and outcome.providers_attempted
            and not outcome.blocked
            and not outcome.errors
        ):
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
        return outcome

    @staticmethod
    def _domain_allowed(url: str, allowed: set[str], denied: set[str]) -> bool:
        domain = (urlparse(url).hostname or "").lower().strip(".")
        if not domain:
            return False
        if any(domain == item or domain.endswith("." + item) for item in denied):
            return False
        if allowed:
            return any(domain == item or domain.endswith("." + item) for item in allowed)
        return True
