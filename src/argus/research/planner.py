from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from argus.config import Settings
from argus.contracts.models import CollectionRequest
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
        "public_mentions": ("", "упоминания"),
        "local_news": ("новости",),
        "incidents": ("происшествия", "авария пожар"),
        "discussions": ("обсуждение форум",),
        "historical_context": (
            "история",
            "что было раньше",
            "снос реконструкция строительство",
            "старый адрес документы публикации",
        ),
    }
    _EN_TERMS: dict[str, tuple[str, ...]] = {
        "reviews": ("reviews", "opinions"),
        "public_mentions": ("", "mentions"),
        "local_news": ("news",),
        "incidents": ("incidents", "accident fire"),
        "discussions": ("discussion forum",),
        "historical_context": (
            "history",
            "what was here before",
            "demolition reconstruction construction",
            "old address documents publications",
        ),
    }

    def __init__(self, *, max_queries: int = 8, max_query_chars: int = 512) -> None:
        self.max_queries = max(1, int(max_queries))
        self.max_query_chars = max(32, int(max_query_chars))

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
        round_index = 0
        while len(queries) < self.max_queries:
            added_this_round = False
            for terms in intent_terms:
                if round_index >= len(terms):
                    continue
                query = self._bounded_query(f'"{territory}" {terms[round_index]}'.strip())
                key = query.casefold()
                if query and key not in seen:
                    seen.add(key)
                    queries.append(query)
                    added_this_round = True
                    if len(queries) >= self.max_queries:
                        break
            if not added_this_round:
                break
            round_index += 1

        return ResearchPlan(
            queries=queries,
            notes=[f"heuristic_language={language}"],
        )

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
        self.fallback = fallback or HeuristicResearchPlanner(max_queries=self.max_queries)

    async def plan(self, request: CollectionRequest) -> ResearchPlan:
        prompt = (
            "You are ARGUS Research Planner. Return strict JSON with keys queries (array of search strings) "
            "and notes (array). Do not invent facts. Plan only how to research public sources. "
            "Cover the requested intents fairly within a small query budget. "
            "For historical context expand current place, former buildings/organizations, construction, "
            "demolition, reconstruction, old addresses, documents, publications and newly discovered entities.\n"
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
                return ResearchPlan(
                    queries=queries,
                    notes=[str(item)[:500] for item in data.get("notes", [])[:20]],
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
