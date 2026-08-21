from __future__ import annotations

from collections.abc import Iterable

from argus.contracts.models import CollectionRequest, Observation


class HistoricalBranchPlanner:
    """Create bounded follow-up research queries from already collected facts.

    The planner never turns inferred entities into facts. It only uses conservative
    labels from Observation fields to seed another normal discovery/fetch/evidence
    cycle. Query de-duplication is collection-scoped through the orchestrator.
    """

    _DATA_KEYS = ("name", "former_name", "old_name", "operator", "brand")

    def __init__(
        self,
        max_queries_per_expansion: int = 3,
        max_total_queries: int = 12,
    ) -> None:
        self.max_queries_per_expansion = max(1, max_queries_per_expansion)
        self.max_total_queries = max(self.max_queries_per_expansion, max_total_queries)

    def expand(
        self,
        request: CollectionRequest,
        observations: Iterable[Observation],
        *,
        seen_queries: set[str],
        limit: int | None = None,
    ) -> list[str]:
        if "historical_context" not in request.intents:
            return []

        remaining_total = max(0, self.max_total_queries - len(seen_queries))
        if remaining_total == 0:
            return []

        territory = self._territory_text(request)
        language = self._language(request, territory)
        requested_limit = self.max_queries_per_expansion if limit is None else max(0, limit)
        query_limit = min(
            requested_limit,
            self.max_queries_per_expansion,
            remaining_total,
        )
        if query_limit == 0:
            return []

        queries: list[str] = []
        local_seen = set(seen_queries)
        for entity in self._entities(observations, territory):
            for query in self._queries(entity, territory, language):
                if query in local_seen:
                    continue
                local_seen.add(query)
                queries.append(query)
                if len(queries) >= query_limit:
                    return queries
        return queries

    def _entities(self, observations: Iterable[Observation], territory: str) -> list[str]:
        entities: list[str] = []
        seen: set[str] = set()
        territory_key = territory.casefold().strip()
        for observation in observations:
            candidates: list[object] = [observation.title]
            candidates.extend(observation.data.get(key) for key in self._DATA_KEYS)
            for raw in candidates:
                if not isinstance(raw, str):
                    continue
                clean = raw.replace('"', " ").replace("\\", " ")
                value = " ".join(clean.split()).strip(" \t\r\n-–—|,.;:")
                if not self._usable_entity(value):
                    continue
                key = value.casefold()
                if key == territory_key or key in seen:
                    continue
                seen.add(key)
                entities.append(value)
        return entities

    @staticmethod
    def _usable_entity(value: str) -> bool:
        if not 3 <= len(value) <= 120:
            return False
        lowered = value.casefold()
        if lowered.startswith(("http://", "https://", "www.")):
            return False
        if value.count("/") > 2:
            return False
        return any(char.isalpha() for char in value)

    @staticmethod
    def _queries(entity: str, territory: str, language: str) -> tuple[str, ...]:
        if language == "ru":
            return (
                f'"{entity}" история "{territory}"',
                f'"{entity}" прежнее название "{territory}"',
                f'"{entity}" строительство реконструкция снос',
            )
        return (
            f'"{entity}" history "{territory}"',
            f'"{entity}" former name "{territory}"',
            f'"{entity}" construction reconstruction demolition',
        )

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
