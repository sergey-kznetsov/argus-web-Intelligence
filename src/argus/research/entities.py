from __future__ import annotations

from collections.abc import Iterable

from argus.contracts.models import CollectionRequest, Observation


class AreaEntityResearchPlanner:
    """Expand nearby factual entities into bounded follow-up web research queries."""

    _AREA_INTENTS = {
        "reviews",
        "comments",
        "complaints",
        "discussions",
        "public_mentions",
        "local_news",
        "incidents",
        "historical_context",
    }
    _ENTITY_SOURCE_KINDS = {
        "map_place",
        "structured_entity",
        "microdata",
        "json_ld",
        "geojson_point",
        "kml_point",
    }
    _DATA_KEYS = ("name", "operator", "brand", "address")

    def __init__(
        self,
        *,
        max_entities_per_expansion: int = 8,
        max_queries_per_entity: int = 3,
        max_total_queries: int = 48,
    ) -> None:
        self.max_entities_per_expansion = max(1, int(max_entities_per_expansion))
        self.max_queries_per_entity = max(1, int(max_queries_per_entity))
        self.max_total_queries = max(1, int(max_total_queries))

    @property
    def area_intents(self) -> set[str]:
        return set(self._AREA_INTENTS)

    def expand(
        self,
        request: CollectionRequest,
        observations: Iterable[Observation],
        *,
        seen_queries: set[str],
        limit: int | None = None,
    ) -> list[str]:
        requested = [intent for intent in request.intents if intent in self._AREA_INTENTS]
        if not requested:
            return []
        remaining = max(0, self.max_total_queries - len(seen_queries))
        if remaining == 0:
            return []
        query_limit = min(remaining, max(0, limit) if limit is not None else remaining)
        if query_limit == 0:
            return []

        territory = self._territory_text(request)
        language = self._language(request, territory)
        queries: list[str] = []
        local_seen = set(seen_queries)
        entities = self._entities(observations)[: self.max_entities_per_expansion]
        for entity in entities:
            entity_queries = self._queries(entity, territory, requested, language)
            added_for_entity = 0
            for query in entity_queries:
                normalized = " ".join(query.split())[:512].rstrip()
                key = normalized.casefold()
                if not normalized or key in local_seen:
                    continue
                local_seen.add(key)
                queries.append(normalized)
                added_for_entity += 1
                if len(queries) >= query_limit:
                    return queries
                if added_for_entity >= self.max_queries_per_entity:
                    break
        return queries

    def _entities(self, observations: Iterable[Observation]) -> list[str]:
        values: list[str] = []
        seen: set[str] = set()
        for observation in observations:
            if observation.source_kind not in self._ENTITY_SOURCE_KINDS:
                continue
            candidates: list[object] = [observation.title]
            candidates.extend(observation.data.get(key) for key in self._DATA_KEYS)
            for raw in candidates:
                if not isinstance(raw, str):
                    continue
                value = self._clean_entity(raw)
                if not self._usable_entity(value):
                    continue
                key = value.casefold()
                if key in seen:
                    continue
                seen.add(key)
                values.append(value)
                break
        return values

    @staticmethod
    def _clean_entity(value: str) -> str:
        value = value.replace('"', " ").replace("\\", " ")
        return " ".join(value.split()).strip(" \t\r\n-–—|,.;:")[:160]

    @staticmethod
    def _usable_entity(value: str) -> bool:
        if not 3 <= len(value) <= 160:
            return False
        lowered = value.casefold()
        if lowered.startswith(("http://", "https://", "www.")):
            return False
        return any(char.isalpha() for char in value)

    @classmethod
    def _queries(
        cls,
        entity: str,
        territory: str,
        intents: list[str],
        language: str,
    ) -> list[str]:
        terms_ru = {
            "reviews": "отзывы мнения",
            "comments": "комментарии",
            "complaints": "жалобы обращения проблемы",
            "discussions": "обсуждение форум жители",
            "public_mentions": "упоминания",
            "local_news": "новости",
            "incidents": "происшествия авария пожар конфликт",
            "historical_context": "история прежнее название что было раньше",
        }
        terms_en = {
            "reviews": "reviews opinions",
            "comments": "comments",
            "complaints": "complaints issues",
            "discussions": "discussion forum residents",
            "public_mentions": "mentions",
            "local_news": "news",
            "incidents": "incidents accident fire conflict",
            "historical_context": "history former name what was here before",
        }
        dictionary = terms_ru if language == "ru" else terms_en
        queries: list[str] = []
        for intent in intents:
            terms = dictionary.get(intent)
            if not terms:
                continue
            queries.append(f'"{entity}" {terms} "{territory}"')
        return queries

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
