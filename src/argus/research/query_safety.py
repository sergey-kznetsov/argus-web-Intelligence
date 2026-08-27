from __future__ import annotations

import ast
import json
import re
from collections.abc import Iterable

from argus.contracts.models import CollectionRequest
from argus.research.followup import FollowupPlan, FollowupResearchPlanner
from argus.research.planner import ResearchPlan, ResearchPlanner

_QUERY_KEYS = ("query", "search_query", "search_string", "queries")
_SERVICE_ONLY_PREFIXES = ("metadata:", "notes:", "query:", "queries:", "search_string:")
_MAX_CONTAINER_DEPTH = 4


def sanitize_research_queries(
    values: object,
    request: CollectionRequest,
    *,
    max_queries: int,
    max_query_chars: int = 512,
    seen_queries: Iterable[str] = (),
) -> list[str]:
    """Normalize untrusted LLM navigation output into bounded search strings.

    Small local models occasionally return objects inside ``queries`` despite being asked
    for an array of strings. Converting those objects with ``str()`` leaks JSON field names
    such as ``queries`` or ``metadata`` into the search engine and can redirect research to
    unrelated technical documentation. ARGUS therefore extracts only an allow-list of query
    fields, ignores notes/metadata, restores the original territorial anchor and rejects bare
    intent/service labels. The result is navigation only and never Evidence.
    """

    limit = max(0, int(max_queries))
    if limit <= 0:
        return []
    char_limit = max(32, int(max_query_chars))
    requested_intents = {
        _normalize_token(intent)
        for intent in request.intents
        if _normalize_token(intent)
    }
    seen = {_normalize_query(item).casefold() for item in seen_queries if str(item).strip()}
    result: list[str] = []

    for raw in _extract_query_values(values):
        query = _normalize_query(raw)
        if not query or _is_service_only_query(query, requested_intents):
            continue
        query = _ensure_territory_anchor(query, request)
        query = query[:char_limit].rstrip()
        key = query.casefold()
        if not query or key in seen:
            continue
        seen.add(key)
        result.append(query)
        if len(result) >= limit:
            break
    return result


class QuerySafeResearchPlanner:
    """Validate any research planner output before it reaches discovery."""

    def __init__(
        self,
        delegate: ResearchPlanner,
        *,
        fallback: ResearchPlanner | None = None,
        max_queries: int = 8,
        max_query_chars: int = 512,
    ) -> None:
        self.delegate = delegate
        self.fallback = fallback
        self.max_queries = max(1, int(max_queries))
        self.max_query_chars = max(32, int(max_query_chars))

    async def plan(self, request: CollectionRequest) -> ResearchPlan:
        plan = await self.delegate.plan(request)
        queries = sanitize_research_queries(
            plan.queries,
            request,
            max_queries=self.max_queries,
            max_query_chars=self.max_query_chars,
        )
        if not queries and self.fallback is not None:
            plan = await self.fallback.plan(request)
            queries = sanitize_research_queries(
                plan.queries,
                request,
                max_queries=self.max_queries,
                max_query_chars=self.max_query_chars,
            )
        plan.queries = queries
        plan.notes = [*plan.notes, "query_safety=query-safety/1"]
        return plan


class QuerySafeFollowupResearchPlanner:
    """Apply the same navigation contract to iterative LLM follow-up queries."""

    def __init__(
        self,
        delegate: FollowupResearchPlanner,
        *,
        fallback: FollowupResearchPlanner | None = None,
        max_query_chars: int = 512,
    ) -> None:
        self.delegate = delegate
        self.fallback = fallback
        self.max_query_chars = max(32, int(max_query_chars))

    async def plan_followups(
        self,
        request: CollectionRequest,
        observations,
        *,
        seen_queries: set[str],
        max_queries: int,
    ) -> FollowupPlan:
        plan = await self.delegate.plan_followups(
            request,
            observations,
            seen_queries=seen_queries,
            max_queries=max_queries,
        )
        queries = sanitize_research_queries(
            plan.queries,
            request,
            max_queries=max_queries,
            max_query_chars=self.max_query_chars,
            seen_queries=seen_queries,
        )
        if not queries and self.fallback is not None:
            plan = await self.fallback.plan_followups(
                request,
                observations,
                seen_queries=seen_queries,
                max_queries=max_queries,
            )
            queries = sanitize_research_queries(
                plan.queries,
                request,
                max_queries=max_queries,
                max_query_chars=self.max_query_chars,
                seen_queries=seen_queries,
            )
        plan.queries = queries
        plan.notes = [*plan.notes, "query_safety=query-safety/1"]
        return plan


def _extract_query_values(value: object, *, depth: int = 0) -> Iterable[str]:
    if depth > _MAX_CONTAINER_DEPTH:
        return
    if isinstance(value, str):
        stripped = value.strip()
        if stripped[:1] in "[{":
            parsed = _parse_container(stripped)
            if parsed is None:
                return
            yield from _extract_query_values(parsed, depth=depth + 1)
            return
        if stripped:
            yield stripped
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from _extract_query_values(item, depth=depth + 1)
        return
    if isinstance(value, dict):
        for key in _QUERY_KEYS:
            if key in value:
                yield from _extract_query_values(value[key], depth=depth + 1)


def _parse_container(value: str) -> object | None:
    if len(value) < 2 or value[0] not in "[{" or value[-1] not in "]}":
        return None
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        try:
            parsed = ast.literal_eval(value)
        except (ValueError, SyntaxError):
            return None
    return parsed if isinstance(parsed, (dict, list, tuple)) else None


def _ensure_territory_anchor(query: str, request: CollectionRequest) -> str:
    anchor = _territory_text(request)
    if not anchor or anchor == "location" or _contains_territory_anchor(query, request):
        return query
    return f'"{anchor}" {query}'.strip()


def _contains_territory_anchor(query: str, request: CollectionRequest) -> bool:
    haystack = _normalize_token(query)
    if not haystack:
        return False
    values = [request.territory.city or "", request.territory.address or ""]
    for raw in values:
        normalized = _normalize_token(raw)
        if normalized and normalized in haystack:
            return True
        tokens = _meaningful_tokens(raw)
        if tokens and any(token in haystack.split() for token in tokens):
            return True
    if request.territory.point is not None:
        latitude = f"{request.territory.point.latitude:.4f}"
        longitude = f"{request.territory.point.longitude:.4f}"
        return latitude in query and longitude in query
    return False


def _territory_text(request: CollectionRequest) -> str:
    city = (request.territory.city or "").strip()
    address = (request.territory.address or "").strip()
    if city and address:
        return address if city.casefold() in address.casefold() else f"{city}, {address}"
    if address:
        return address
    if city:
        return city
    if request.territory.point is not None:
        return (
            f"{request.territory.point.latitude:.6f},"
            f"{request.territory.point.longitude:.6f}"
        )
    return "location"


def _is_service_only_query(query: str, requested_intents: set[str]) -> bool:
    normalized = _normalize_token(query)
    if not normalized:
        return True
    if normalized in requested_intents:
        return True
    lowered = query.casefold().strip()
    return lowered.startswith(_SERVICE_ONLY_PREFIXES)


def _meaningful_tokens(value: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[\w-]+", _normalize_token(value), flags=re.UNICODE)
        if len(token) >= 3 or token.isdigit()
    ]


def _normalize_query(value: object) -> str:
    return " ".join(str(value).split()).strip()


def _normalize_token(value: object) -> str:
    return " ".join(re.findall(r"[\w-]+", str(value).casefold(), flags=re.UNICODE))
