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
    async def plan(self, request: CollectionRequest) -> ResearchPlan:
        territory = request.territory.address or request.territory.city or "location"
        queries: list[str] = []
        for intent in request.intents:
            if intent == "historical_context":
                queries.extend([
                    f'"{territory}" история',
                    f'"{territory}" что было раньше',
                    f'"{territory}" снос реконструкция строительство',
                ])
            else:
                queries.append(f'"{territory}" {intent.replace("_", " ")}')
        return ResearchPlan(queries=queries)


class OllamaResearchPlanner:
    def __init__(self, settings: Settings, fallback: ResearchPlanner | None = None) -> None:
        self.settings = settings
        self.fallback = fallback or HeuristicResearchPlanner()

    async def plan(self, request: CollectionRequest) -> ResearchPlan:
        prompt = (
            "You are ARGUS Research Planner. Return strict JSON with keys queries (array of search strings) "
            "and notes (array). Do not invent facts. Plan only how to research public sources. "
            "For historical context expand current place, former buildings/organizations, construction, "
            "demolition, reconstruction, old addresses, documents, publications and newly discovered entities.\n"
            f"Input: {request.model_dump_json()}"
        )
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(
                    f"{self.settings.ollama_url.rstrip('/')}/api/generate",
                    json={"model": self.settings.ollama_model, "prompt": prompt, "stream": False, "format": "json"},
                )
                response.raise_for_status()
                raw = response.json().get("response", "{}")
                data: dict[str, Any] = json.loads(raw)
                queries = [str(item) for item in data.get("queries", []) if str(item).strip()]
                if not queries:
                    return await self.fallback.plan(request)
                return ResearchPlan(queries=queries, notes=[str(x) for x in data.get("notes", [])])
        except (httpx.HTTPError, ValueError, json.JSONDecodeError):
            return await self.fallback.plan(request)
