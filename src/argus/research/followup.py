from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

from argus.config import Settings
from argus.contracts.models import CollectionRequest, Observation


@dataclass(slots=True)
class FollowupPlan:
    queries: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class FollowupResearchPlanner(Protocol):
    async def plan_followups(
        self,
        request: CollectionRequest,
        observations: list[Observation],
        *,
        seen_queries: set[str],
        max_queries: int,
    ) -> FollowupPlan: ...


class HeuristicFollowupResearchPlanner:
    _RU_TERMS = {
        "reviews": "отзывы мнения оценки",
        "comments": "комментарии",
        "complaints": "жалобы обращения проблемы",
        "discussions": "обсуждение форум жители",
        "public_mentions": "упоминания публикации",
        "local_news": "новости СМИ",
        "incidents": "происшествия авария пожар конфликт",
        "historical_context": "история архив что было раньше реконструкция снос строительство",
    }
    _EN_TERMS = {
        "reviews": "reviews opinions ratings",
        "comments": "comments",
        "complaints": "complaints issues",
        "discussions": "discussion forum residents",
        "public_mentions": "mentions publications",
        "local_news": "news media",
        "incidents": "incidents accident fire conflict",
        "historical_context": "history archive what was here before reconstruction demolition construction",
    }

    def __init__(self, *, target_hits_per_intent: int = 2) -> None:
        self.target_hits_per_intent = max(1, int(target_hits_per_intent))

    async def plan_followups(
        self,
        request: CollectionRequest,
        observations: list[Observation],
        *,
        seen_queries: set[str],
        max_queries: int,
    ) -> FollowupPlan:
        if max_queries <= 0:
            return FollowupPlan()
        territory = self._territory_text(request)
        language = self._language(request, territory)
        dictionary = self._RU_TERMS if language == "ru" else self._EN_TERMS
        counts = self._intent_counts(observations)
        queries: list[str] = []
        notes: list[str] = []
        seen = {item.casefold() for item in seen_queries}

        for intent in request.intents:
            terms = dictionary.get(intent)
            if not terms:
                continue
            current = counts.get(intent, 0)
            if current >= self.target_hits_per_intent:
                continue
            query = f'"{territory}" {terms}'
            key = query.casefold()
            if key in seen:
                continue
            seen.add(key)
            queries.append(query[:512])
            notes.append(f"coverage_gap:{intent}:{current}")
            if len(queries) >= max_queries:
                break

        if "historical_context" in request.intents and not any(
            item.source_kind in {"historical_page_version", "historical_entity_change"}
            for item in observations
        ):
            query = (
                f'"{territory}" архив старые фотографии старый адрес история'
                if language == "ru"
                else f'"{territory}" archive old photos old address history'
            )
            key = query.casefold()
            if key not in seen and len(queries) < max_queries:
                queries.append(query[:512])
                notes.append("coverage_gap:historical_timeline")
        return FollowupPlan(queries=queries, notes=notes)

    @staticmethod
    def _intent_counts(observations: list[Observation]) -> dict[str, int]:
        urls_by_goal: dict[str, set[str]] = {}
        for observation in observations:
            goals: list[str] = []
            raw_data = observation.data.get("research_goals")
            if isinstance(raw_data, list):
                goals.extend(str(item) for item in raw_data)
            raw_provenance = observation.provenance.get("research_goals")
            if isinstance(raw_provenance, list):
                goals.extend(str(item) for item in raw_provenance)
            if observation.source_kind in {"historical_page_version", "historical_entity_change"}:
                goals.append("historical_context")
            source_identity = observation.url or observation.observation_id
            for goal in set(goals):
                urls_by_goal.setdefault(goal, set()).add(source_identity)
        return {goal: len(urls) for goal, urls in urls_by_goal.items()}

    @staticmethod
    def _territory_text(request: CollectionRequest) -> str:
        city = (request.territory.city or "").strip()
        address = (request.territory.address or "").strip()
        if city and address:
            return address if city.casefold() in address.casefold() else f"{city}, {address}"
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
        return (
            "ru"
            if any("а" <= char.lower() <= "я" or char.lower() == "ё" for char in territory)
            else "en"
        )


class OllamaFollowupResearchPlanner:
    """Use local LLM only to identify missing search directions, never to create facts."""

    def __init__(
        self,
        settings: Settings,
        fallback: FollowupResearchPlanner | None = None,
    ) -> None:
        self.settings = settings
        self.fallback = fallback or HeuristicFollowupResearchPlanner()
        self.max_observations = 60
        self.max_summary_chars = 24_000
        self.max_query_chars = 512

    async def plan_followups(
        self,
        request: CollectionRequest,
        observations: list[Observation],
        *,
        seen_queries: set[str],
        max_queries: int,
    ) -> FollowupPlan:
        if max_queries <= 0:
            return FollowupPlan()
        summary = self._summary(observations)
        prompt = (
            "You are ARGUS iterative research planner. Your job is to find gaps in web research, "
            "not to answer the research question and not to invent facts. Return strict JSON with "
            "keys queries (array of search strings) and notes (array). Use only the supplied factual "
            "coverage summary to decide what still needs to be searched. Prefer missing source types, "
            "nearby entities, reviews/comments/complaints/discussions, local media and historical "
            "records when requested. Do not repeat queries from seen_queries. Keep queries precise.\n"
            f"Maximum queries: {max_queries}\n"
            f"Request: {request.model_dump_json()}\n"
            f"Seen queries: {json.dumps(sorted(seen_queries)[-100:], ensure_ascii=False)}\n"
            f"Coverage summary: {json.dumps(summary, ensure_ascii=False)}"
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
                queries = self._bounded_queries(
                    data.get("queries", []),
                    seen_queries=seen_queries,
                    max_queries=max_queries,
                )
                if not queries:
                    return await self.fallback.plan_followups(
                        request,
                        observations,
                        seen_queries=seen_queries,
                        max_queries=max_queries,
                    )
                notes_raw = data.get("notes", [])
                notes = (
                    [str(item)[:500] for item in notes_raw[:20]]
                    if isinstance(notes_raw, list)
                    else []
                )
                return FollowupPlan(queries=queries, notes=notes)
        except (httpx.HTTPError, ValueError, json.JSONDecodeError, TypeError):
            return await self.fallback.plan_followups(
                request,
                observations,
                seen_queries=seen_queries,
                max_queries=max_queries,
            )

    def _summary(self, observations: list[Observation]) -> list[dict[str, object]]:
        result: list[dict[str, object]] = []
        used_chars = 0
        for observation in observations[-self.max_observations :]:
            host = urlsplit(observation.url).hostname or ""
            goals = observation.data.get("research_goals", [])
            if not isinstance(goals, list):
                goals = observation.provenance.get("research_goals", [])
            item: dict[str, object] = {
                "source": observation.source,
                "source_kind": observation.source_kind,
                "entity_type": observation.entity_type,
                "title": (observation.title or "")[:300],
                "host": host[:253],
                "published_at": (
                    observation.published_at.isoformat() if observation.published_at else None
                ),
                "research_goals": goals if isinstance(goals, list) else [],
            }
            encoded = json.dumps(item, ensure_ascii=False)
            if used_chars + len(encoded) > self.max_summary_chars:
                break
            used_chars += len(encoded)
            result.append(item)
        return result

    def _bounded_queries(
        self,
        values: object,
        *,
        seen_queries: set[str],
        max_queries: int,
    ) -> list[str]:
        if not isinstance(values, list):
            return []
        seen = {item.casefold() for item in seen_queries}
        result: list[str] = []
        for item in values:
            normalized = " ".join(str(item).split()).strip()[: self.max_query_chars].rstrip()
            key = normalized.casefold()
            if not normalized or key in seen:
                continue
            seen.add(key)
            result.append(normalized)
            if len(result) >= max_queries:
                break
        return result
