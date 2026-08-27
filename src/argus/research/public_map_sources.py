from __future__ import annotations

from dataclasses import dataclass

from argus.contracts.models import CollectionRequest, Observation
from argus.normalization.public_map_provenance import classify_public_map_url
from argus.research.intent_coverage import IntentCoverageEvaluator


@dataclass(frozen=True, slots=True)
class PublicMapSourceProfile:
    source_id: str
    domain_scope: str
    kind: str
    priority: int


PUBLIC_MAP_SOURCES: tuple[PublicMapSourceProfile, ...] = (
    PublicMapSourceProfile("yandex_maps_web", "yandex.ru/maps", "map_cards_reviews", 10),
    PublicMapSourceProfile("2gis_web", "2gis.ru", "map_cards_reviews", 20),
    PublicMapSourceProfile("google_maps_web", "google.com/maps", "map_cards_reviews", 30),
)


class PublicMapSourceResearchPlanner:
    """Target public map/card pages through normal web discovery, never paid map APIs.

    Curated map research is driven by factual coverage gaps. Navigation metadata and
    successfully opened card shells do not stop research. Only observations from known
    public-map web surfaces that actually evidence a requested intent count toward the
    target. This keeps the planner aligned with ARGUS' evidence-first research lifecycle.
    """

    version = "public-map-sources/3"
    supported_intents = frozenset(
        {
            "reviews",
            "comments",
            "complaints",
            "discussions",
        }
    )

    def __init__(
        self,
        sources: tuple[PublicMapSourceProfile, ...] = PUBLIC_MAP_SOURCES,
        *,
        max_anchor_chars: int = 180,
        target_sources_per_intent: int = 2,
        coverage: IntentCoverageEvaluator | None = None,
    ) -> None:
        self.sources = tuple(sorted(sources, key=lambda item: (item.priority, item.source_id)))
        self.max_anchor_chars = max(32, int(max_anchor_chars))
        self.target_sources_per_intent = max(1, int(target_sources_per_intent))
        self.coverage = coverage or IntentCoverageEvaluator()

    def queries(
        self,
        request: CollectionRequest,
        *,
        observations: list[Observation] | None = None,
        seen_queries: set[str] | None = None,
        limit: int = 3,
    ) -> list[str]:
        if limit <= 0:
            return []
        observations = observations or []
        remaining_intents = self.remaining_intents(request, observations)
        if not remaining_intents:
            return []
        seen = {
            " ".join(value.split()).casefold()
            for value in (seen_queries or set())
            if value.strip()
        }
        anchors = self._anchors(request, observations)
        if not anchors:
            return []
        language = self._language(request, anchors[0])
        suffix = self._suffix(remaining_intents, language)

        result: list[str] = []
        for anchor in anchors:
            for profile in self.sources:
                query = self._query(profile, anchor, suffix)
                key = " ".join(query.split()).casefold()
                if key in seen:
                    continue
                seen.add(key)
                result.append(query)
                if len(result) >= limit:
                    return result
        return result

    def coverage_counts(
        self,
        request: CollectionRequest,
        observations: list[Observation],
    ) -> dict[str, int]:
        """Count independent territorially relevant public-map factual sources."""

        requested = self._requested_intents(request)
        if not requested:
            return {}
        map_observations = [
            observation
            for observation in observations
            if classify_public_map_url(observation.url) is not None
        ]
        counts = self.coverage.counts(map_observations, request=request)
        return {intent: int(counts.get(intent, 0)) for intent in requested}

    def remaining_intents(
        self,
        request: CollectionRequest,
        observations: list[Observation],
    ) -> list[str]:
        counts = self.coverage_counts(request, observations)
        return [
            intent
            for intent in self._requested_intents(request)
            if counts.get(intent, 0) < self.target_sources_per_intent
        ]

    def source_metadata(self) -> list[dict[str, object]]:
        return [
            {
                "source_id": item.source_id,
                "domain_scope": item.domain_scope,
                "kind": item.kind,
                "priority": item.priority,
                "access": "public_web_browser",
                "paid_api": False,
            }
            for item in self.sources
        ]

    def _requested_intents(self, request: CollectionRequest) -> list[str]:
        return list(
            dict.fromkeys(
                intent
                for intent in request.intents
                if intent in self.supported_intents
            )
        )

    def _anchors(
        self,
        request: CollectionRequest,
        observations: list[Observation],
    ) -> list[str]:
        values: list[str] = []
        seen: set[str] = set()
        territory = self._territory_text(request)
        if territory:
            values.append(territory)
            seen.add(territory.casefold())
        for observation in observations:
            for raw in (
                observation.title,
                observation.data.get("name"),
                observation.data.get("brand"),
                observation.data.get("operator"),
                observation.data.get("address"),
            ):
                if not isinstance(raw, str):
                    continue
                value = self._clean_anchor(raw)
                if value is None or value.casefold() in seen:
                    continue
                seen.add(value.casefold())
                values.append(value)
            if len(values) >= 12:
                break
        return values

    @staticmethod
    def _suffix(intents: list[str], language: str) -> str:
        requested = set(intents)
        if language == "ru":
            terms: list[str] = []
            if "reviews" in requested:
                terms.append("отзывы")
            if "comments" in requested:
                terms.append("комментарии")
            if "complaints" in requested:
                terms.append("жалобы")
            if "discussions" in requested:
                terms.append("обсуждения")
            return " ".join(terms[:3]) or "отзывы рейтинг"
        terms = []
        if "reviews" in requested:
            terms.append("reviews")
        if "comments" in requested:
            terms.append("comments")
        if "complaints" in requested:
            terms.append("complaints")
        if "discussions" in requested:
            terms.append("discussion")
        return " ".join(terms[:3]) or "reviews rating"

    @staticmethod
    def _query(profile: PublicMapSourceProfile, anchor: str, suffix: str) -> str:
        return f'site:{profile.domain_scope} "{anchor}" {suffix}'[:512].rstrip()

    def _clean_anchor(self, value: str) -> str | None:
        clean = " ".join(value.replace('"', " ").replace("\\", " ").split()).strip()
        if not clean or len(clean) < 3:
            return None
        return clean[: self.max_anchor_chars].rstrip()

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
        return ""

    @staticmethod
    def _language(request: CollectionRequest, anchor: str) -> str:
        configured = (request.constraints.language or "").lower()
        if configured.startswith("ru"):
            return "ru"
        if configured.startswith("en"):
            return "en"
        return (
            "ru"
            if any("а" <= char.lower() <= "я" or char.lower() == "ё" for char in anchor)
            else "en"
        )