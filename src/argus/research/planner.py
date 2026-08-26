from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from argus.config import Settings
from argus.contracts.models import CollectionRequest
from argus.research.historical_sources import HistoricalSourceResearchPlanner
from argus.research.public_map_sources import PublicMapSourceResearchPlanner
from argus.sources.base import SourceTask


@dataclass(slots=True)
class ResearchPlan:
    queries: list[str] = field(default_factory=list)
    tasks: list[SourceTask] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class ResearchPlanner(Protocol):
    async def plan(self, request: CollectionRequest) -> ResearchPlan: ...


def _source_pool_tasks(request: CollectionRequest) -> list[SourceTask]:
    """Turn caller-supplied public URLs into normal research candidates.

    ``source_pool_urls`` supplement ARGUS discovery. They are deliberately emitted as
    planner tasks (merged after normal initial/discovery work) rather than seed tasks,
    so a caller-provided URL does not become a privileged source. The destination still
    passes the normal URL guard, FAST/BROWSER/AGENT lifecycle and Evidence extraction.
    """

    goals = list(dict.fromkeys(str(intent).strip() for intent in request.intents if intent.strip()))
    if not goals:
        return []
    tasks: list[SourceTask] = []
    seen: set[str] = set()
    for raw_url in request.constraints.source_pool_urls:
        url = str(raw_url)
        if url in seen:
            continue
        seen.add(url)
        tasks.append(
            SourceTask(
                source_id="generic_web",
                goal=goals[0],
                url=url,
                depth=0,
                metadata={
                    "research_goals": goals,
                    "allowed_domains": list(request.constraints.allowed_domains),
                    "source_pool": {
                        "kind": "supplemental",
                        "caller_supplied": True,
                        "priority": "normal",
                        "navigation_only": True,
                        "is_evidence": False,
                    },
                },
            )
        )
    return tasks


def _merge_curated_sources(
    request: CollectionRequest,
    queries: list[str],
    *,
    max_queries: int,
    max_query_chars: int,
    protected_count: int,
    historical_sources: HistoricalSourceResearchPlanner,
    public_map_sources: PublicMapSourceResearchPlanner,
) -> tuple[list[str], int, int]:
    if max_queries <= 0:
        return [], 0, 0

    max_query_chars = max(32, int(max_query_chars))
    protected_count = min(max(0, int(protected_count)), len(queries), max_queries)
    protected = list(queries[:protected_count])
    secondary = list(queries[protected_count:max_queries])

    historical: list[str] = []
    if "historical_context" in request.intents:
        historical = [
            query[:max_query_chars].rstrip()
            for query in historical_sources.queries(
                request,
                limit=min(4, max(1, max_queries // 2)),
            )
        ]

    public_maps: list[str] = []
    if public_map_sources.supported_intents.intersection(request.intents):
        public_maps = [
            query[:max_query_chars].rstrip()
            for query in public_map_sources.queries(
                request,
                limit=min(3, max(1, max_queries // 2)),
            )
        ]

    if not historical and not public_maps:
        return queries[:max_queries], 0, 0

    curated_budget = max(0, max_queries - len(protected))
    curated: list[tuple[str, str]] = []
    indexes = {"historical": 0, "public_map": 0}
    groups = {"historical": historical, "public_map": public_maps}
    while len(curated) < curated_budget:
        progressed = False
        for name in ("historical", "public_map"):
            index = indexes[name]
            values = groups[name]
            if index >= len(values):
                continue
            curated.append((name, values[index]))
            indexes[name] = index + 1
            progressed = True
            if len(curated) >= curated_budget:
                break
        if not progressed:
            break

    result: list[str] = []
    seen: set[str] = set()
    for query in protected:
        key = query.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(query)

    historical_count = 0
    public_map_count = 0
    for category, query in curated:
        key = query.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(query)
        if category == "historical":
            historical_count += 1
        else:
            public_map_count += 1
        if len(result) >= max_queries:
            return result, historical_count, public_map_count

    for query in secondary:
        key = query.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(query)
        if len(result) >= max_queries:
            break
    return result, historical_count, public_map_count


class HeuristicResearchPlanner:
    _RU_TERMS: dict[str, tuple[str, ...]] = {
        "reviews": ("отзывы", "мнения"),
        "comments": ("комментарии", "комментарии жителей"),
        "complaints": ("жалобы обращения", "проблемы жителей"),
        "public_mentions": ("", "упоминания"),
        "local_news": ("новости",),
        "incidents": ("происшествия", "авария пожар"),
        "discussions": ("обсуждение форум", "жители обсуждают"),
        "historical_context": (
            "история",
            "что было раньше",
            "снос реконструкция строительство",
            "старый адрес документы публикации",
        ),
    }
    _EN_TERMS: dict[str, tuple[str, ...]] = {
        "reviews": ("reviews", "opinions"),
        "comments": ("comments", "resident comments"),
        "complaints": ("complaints", "resident issues"),
        "public_mentions": ("", "mentions"),
        "local_news": ("news",),
        "incidents": ("incidents", "accident fire"),
        "discussions": ("discussion forum", "residents discuss"),
        "historical_context": (
            "history",
            "what was here before",
            "demolition reconstruction construction",
            "old address documents publications",
        ),
    }

    def __init__(
        self,
        *,
        max_queries: int = 8,
        max_query_chars: int = 512,
        historical_sources: HistoricalSourceResearchPlanner | None = None,
        public_map_sources: PublicMapSourceResearchPlanner | None = None,
    ) -> None:
        self.max_queries = max(1, int(max_queries))
        self.max_query_chars = max(32, int(max_query_chars))
        self.historical_sources = historical_sources or HistoricalSourceResearchPlanner()
        self.public_map_sources = public_map_sources or PublicMapSourceResearchPlanner()

    async def plan(self, request: CollectionRequest) -> ResearchPlan:
        territory = self._territory_text(request)
        language = self._language(request, territory)
        dictionary = self._RU_TERMS if language == "ru" else self._EN_TERMS
        intent_terms: list[tuple[str, ...]] = []
        for intent in request.intents:
            terms = dictionary.get(intent)
            if terms is None:
                terms = (intent.replace("_", " "),)
            intent_terms.append(terms)

        queries: list[str] = []
        seen: set[str] = set()
        primary_count = 0
        max_rounds = max((len(terms) for terms in intent_terms), default=0)
        for round_index in range(max_rounds):
            for terms in intent_terms:
                if round_index >= len(terms):
                    continue
                query = self._bounded_query(f'"{territory}" {terms[round_index]}'.strip())
                key = query.casefold()
                if query and key not in seen:
                    seen.add(key)
                    queries.append(query)
                    if round_index == 0:
                        primary_count += 1
                    if len(queries) >= self.max_queries:
                        break
            if len(queries) >= self.max_queries:
                break

        queries, historical_count, public_map_count = _merge_curated_sources(
            request,
            queries,
            max_queries=self.max_queries,
            max_query_chars=self.max_query_chars,
            protected_count=primary_count,
            historical_sources=self.historical_sources,
            public_map_sources=self.public_map_sources,
        )
        notes = [f"heuristic_language={language}"]
        if historical_count:
            notes.append(
                f"curated_historical_sources={historical_count};"
                f"version={self.historical_sources.version}"
            )
        if public_map_count:
            notes.append(
                f"curated_public_map_sources={public_map_count};"
                f"version={self.public_map_sources.version}"
            )
        source_pool_tasks = _source_pool_tasks(request)
        if source_pool_tasks:
            notes.append(f"supplemental_source_pool={len(source_pool_tasks)};priority=normal")
        return ResearchPlan(queries=queries, tasks=source_pool_tasks, notes=notes)

    def _bounded_query(self, value: str) -> str:
        normalized = " ".join(value.split()).strip()
        return normalized[: self.max_query_chars].rstrip()

    @staticmethod
    def _territory_text(request: CollectionRequest) -> str:
        city = (request.territory.city or "").strip()
        address = (request.territory.address or "").strip()
        if city and address:
            if city.casefold() in address.casefold():
                return address
            return f"{city}, {address}"
        if address:
            return address
        if city:
            return city
        if request.territory.point:
            return (
                f"{request.territory.point.latitude:.6f},"
                f"{request.territory.point.longitude:.6f}"
            )
        return "location"

    @staticmethod
    def _language(request: CollectionRequest, territory: str) -> str:
        configured = (request.constraints.language or "").lower()
        if configured.startswith("ru"):
            return "ru"
        if configured.startswith("en"):
            return "en"
        if any("а" <= char.lower() <= "я" or char.lower() == "ё" for char in territory):
            return "ru"
        return "en"


class OllamaResearchPlanner:
    def __init__(self, settings: Settings, fallback: ResearchPlanner | None = None) -> None:
        self.settings = settings
        self.max_queries = max(1, int(settings.discovery_max_queries))
        self.max_query_chars = 512
        self.historical_sources = HistoricalSourceResearchPlanner(
            catalog_file=settings.historical_source_catalog_file
        )
        self.public_map_sources = PublicMapSourceResearchPlanner()
        self.fallback = fallback or HeuristicResearchPlanner(
            max_queries=self.max_queries,
            max_query_chars=self.max_query_chars,
            historical_sources=self.historical_sources,
            public_map_sources=self.public_map_sources,
        )

    async def plan(self, request: CollectionRequest) -> ResearchPlan:
        prompt = (
            "You are ARGUS Research Planner. Return strict JSON with keys queries (array of search strings) "
            "and notes (array). Do not invent facts. Plan only how to research public sources. "
            "Cover the requested intents fairly within a small query budget. For area research include "
            "nearby organizations/places and, when requested, reviews, comments, complaints, resident "
            "discussions, local media, incidents and historical records. Public map/card pages are "
            "ordinary public web sources: do not assume paid map APIs or access-control bypass. Caller "
            "supplied source_pool_urls are supplemental candidates, not authoritative or prioritized "
            "sources; do not reduce normal discovery because they exist. For historical context expand "
            "current place, former buildings/organizations, construction, demolition, reconstruction, "
            "old addresses, documents, publications and newly discovered entities.\n"
            f"Input: {request.model_dump_json()}"
        )
        try:
            async with httpx.AsyncClient(timeout=20.0, trust_env=False) as client:
                response = await client.post(
                    f"{self.settings.ollama_url.rstrip('/')}/api/generate",
                    json={
                        "model": self.settings.ollama_model,
                        "prompt": prompt,
                        "stream": False,
                        "format": "json",
                    },
                )
                response.raise_for_status()
                raw = response.json().get("response", "{}")
                data: dict[str, Any] = json.loads(raw)
                queries = self._bounded_queries(data.get("queries", []))
                if not queries:
                    return await self.fallback.plan(request)
                protected_count = min(
                    len(queries),
                    len({intent.casefold() for intent in request.intents}),
                )
                queries, historical_count, public_map_count = _merge_curated_sources(
                    request,
                    queries,
                    max_queries=self.max_queries,
                    max_query_chars=self.max_query_chars,
                    protected_count=protected_count,
                    historical_sources=self.historical_sources,
                    public_map_sources=self.public_map_sources,
                )
                notes = self._bounded_notes(data.get("notes", []))
                if historical_count:
                    notes.append(
                        f"curated_historical_sources={historical_count};"
                        f"version={self.historical_sources.version}"
                    )
                if public_map_count:
                    notes.append(
                        f"curated_public_map_sources={public_map_count};"
                        f"version={self.public_map_sources.version}"
                    )
                source_pool_tasks = _source_pool_tasks(request)
                if source_pool_tasks:
                    notes.append(
                        f"supplemental_source_pool={len(source_pool_tasks)};priority=normal"
                    )
                return ResearchPlan(
                    queries=queries,
                    tasks=source_pool_tasks,
                    notes=notes,
                )
        except (httpx.HTTPError, ValueError, json.JSONDecodeError, TypeError):
            return await self.fallback.plan(request)

    def _bounded_queries(self, values: object) -> list[str]:
        if not isinstance(values, list):
            return []
        result: list[str] = []
        seen: set[str] = set()
        for item in values:
            normalized = " ".join(str(item).split()).strip()[: self.max_query_chars].rstrip()
            key = normalized.casefold()
            if not normalized or key in seen:
                continue
            seen.add(key)
            result.append(normalized)
            if len(result) >= self.max_queries:
                break
        return result

    @staticmethod
    def _bounded_notes(values: object) -> list[str]:
        if not isinstance(values, list):
            return []
        return [str(item)[:500] for item in values[:20]]
