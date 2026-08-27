from __future__ import annotations

import re
from urllib.parse import unquote

from argus.research.discovery import DiscoveryHit, _PreparedHit
from argus.research.task_context import ResearchInputDiscoveryService


class TerritoryAwareDiscoveryService(ResearchInputDiscoveryService):
    """Prefer search destinations that visibly match territory/research semantics.

    Search-engine rank is useful but cannot outrank an explicit territorial match. ARGUS
    therefore scores title/URL navigation metadata before fetching a page. If a provider
    batch contains clearly relevant candidates, zero-signal candidates are reduced to one
    bounded fallback rather than consuming the whole crawl budget. This stage is navigation
    only: it never creates factual Evidence and never trusts search snippets as facts.
    """

    ranking_version = "discovery-ranking/2"
    telemetry_version = "discovery-telemetry/3"
    zero_signal_policy = "keep_one_fallback_when_positive_candidates_exist"

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
        prepared = await super()._prepare_hits(
            hits,
            allowed=allowed,
            denied=denied,
            allowed_order=allowed_order,
            locality_tokens=locality_tokens,
            seen=seen,
        )
        if len(prepared) <= 1:
            return prepared

        positive: list[_PreparedHit] = []
        neutral: list[_PreparedHit] = []
        for candidate in prepared:
            if self._relevance_signal(candidate) > 0:
                positive.append(candidate)
            else:
                neutral.append(candidate)
        if not positive:
            return prepared
        return [*positive, *neutral[:1]]

    @classmethod
    def _ranking_key(cls, candidate: _PreparedHit) -> tuple[int, int, int, int, str]:
        provider_rank = candidate.hit.rank if candidate.hit.rank is not None else 1_000_000
        relevance = cls._relevance_signal(candidate)
        return (
            candidate.domain_priority,
            -relevance,
            provider_rank,
            0 if candidate.https else 1,
            candidate.canonical_url,
        )

    @classmethod
    def _relevance_signal(cls, candidate: _PreparedHit) -> int:
        return candidate.locality_matches * 20 + cls._query_match_count(candidate)

    @classmethod
    def _query_match_count(cls, candidate: _PreparedHit) -> int:
        query = candidate.hit.query or ""
        if not query.strip():
            return 0
        haystack = " ".join(
            (candidate.hit.title or "", unquote(candidate.canonical_url))
        ).casefold()
        tokens = cls._query_tokens(query)
        return sum(1 for token in tokens if cls._contains_token(haystack, token))

    @staticmethod
    def _query_tokens(query: str) -> tuple[str, ...]:
        result: list[str] = []
        seen: set[str] = set()
        for token in re.findall(r"[\w-]+", query.casefold(), flags=re.UNICODE):
            token = token.strip("-")
            if len(token) < 4 or token.isdigit() or token in seen:
                continue
            if token in {"site", "http", "https", "www", "search", "query"}:
                continue
            seen.add(token)
            result.append(token)
            if len(result) >= 16:
                break
        return tuple(result)

    @staticmethod
    def _contains_token(haystack: str, token: str) -> bool:
        return re.search(rf"(?<!\w){re.escape(token)}(?!\w)", haystack) is not None
