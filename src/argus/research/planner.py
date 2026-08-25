from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from argus.config import Settings
from argus.contracts.models import CollectionRequest
from argus.research.historical_sources import HistoricalSourceResearchPlanner
from argus.sources.base import SourceTask


@dataclass(slots=True)
class ResearchPlan:
    queries: list[str] = field(default_factory=list)
    tasks: list[SourceTask] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class ResearchPlanner(Protocol):
    async def plan(self, request: CollectionRequest) -> ResearchPlan: ...


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
    ) -> None:
        self.max_queries = max(1, int(max_queries))
        self.max_query_chars = max(32, int(max_query_chars))
        self.historical_sources = historical_sources or HistoricalSourceResearchPlanner()

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
                    if len(queries) >= self.max_queries:
                        break
            if len(queries) >= self.max_queries:
                break

        queries, historical_count = self._merge_curated_historical(request, queries)
        notes = [f"heuristic_language={language}"]
        if historical_count:
            notes.append(
                f"curated_historical_sources={historical_count};"
                f"version={self.historical_sources.version}"
            )
        return ResearchPlan(queries=queries, notes=notes)

    def _merge_curated_historical(
        self,
        request: CollectionRequest,
        queries: list[str],
    ) -> tuple[list[str], int]:
        if "historical_context" not in request.intents:
            return queries[: self.max_queries], 0
        reserve = min(4, max(1, self.max_queries // 2))
        curated = self.historical_sources.queries(request, limit=reserve)
        if not curated:
            return queries[: self.max_queries], 0
        keep = max(0, self.max_queries - len(curated))
        result = list(queries[:keep])
        seen = {query.casefold() for query in result}
        added = 0
        for query in curated:
            if query.casefold() in seen:
                continue
            seen.add(query.casefold())
            result.append(query)
            added += 1
            if len(result) >= self.max_queries:
                break
        return result, added

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
        self.historical_sources = HistoricalSourceResearchPlanner()
        self.fallback = fallback or HeuristicResearchPlanner(
            max_queries=self.max_queries,
            historical_sources=self.historical_sources,
        )

    async def plan(self, request: CollectionRequest) -> ResearchPlan:
        prompt = (
            "You are ARGUS Research Planner. Return strict JSON with keys queries (array of search strings) "
            "and notes (array). Do not invent facts. Plan only how to research public sources. "
            "Cover the requested intents fairly within a small query budget. For area research include "
            "nearby organizations/places and, when requested, reviews, comments, complaints, resident "
            "discussions, local media, incidents and historical records. For historical context expand "
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
                queries, historical_count = self._merge_curated_historical(request, queries)
                notes = self._bounded_notes(data.get("notes", []))
                if historical_count:
                    notes.append(
                        f"curated_historical_sources={historical_count};"
                        f"version={self.historical_sources.version}"
                    )
                return ResearchPlan(queries=queries, notes=notes)
        except (httpx.HTTPError, ValueError, json.JSONDecodeError, TypeError):
            return await self.fallback.plan(request)

    def _merge_curated_historical(
        self,
        request: CollectionRequest,
        queries: list[str],
    ) -> tuple[list[str], int]:
        if "historical_context" not in request.intents:
            return queries[: self.max_queries], 0
        reserve = min(4, max(1, self.max_queries // 2))
        curated = self.historical_sources.queries(request, limit=reserve)
        keep = max(0, self.max_queries - len(curated))
        result = list(queries[:keep])
        seen = {query.casefold() for query in result}
        added = 0
        for query in curated:
            key = query.casefold()
            if key in seen:
                continue
            seen.add(key)
            result.append(query)
            added += 1
            if len(result) >= self.max_queries:
                break
        return result, added

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
