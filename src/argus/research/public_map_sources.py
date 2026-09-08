from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote, quote_plus

from argus.contracts.models import CollectionRequest, Observation
from argus.normalization.public_map_provenance import classify_public_map_url
from argus.research.intent_coverage import IntentCoverageEvaluator
from argus.research.radius_scope import (
    nearby_radius_street_names,
    radius_scope_text,
    radius_street_text,
)
from argus.sources.base import SourceTask
from argus.toolpacks import resolved_tool_pack_from_request


@dataclass(frozen=True, slots=True)
class PublicMapSourceProfile:
    source_id: str
    domain_scope: str
    kind: str
    priority: int
    direct_search_mode: str | None = None


PUBLIC_MAP_SOURCES: tuple[PublicMapSourceProfile, ...] = (
    PublicMapSourceProfile("yandex_maps_web", "yandex.ru/maps", "map_cards_ugc", 10),
    PublicMapSourceProfile(
        "2gis_web",
        "2gis.ru",
        "map_cards_ugc",
        20,
        direct_search_mode="2gis_path",
    ),
    PublicMapSourceProfile(
        "google_maps_web",
        "google.com/maps",
        "map_cards_ugc",
        30,
        direct_search_mode="google_maps_url",
    ),
)


class PublicMapSourceResearchPlanner:
    """Discover public map UGC through the normal ARGUS web research contour.

    Search-engine discovery remains useful, but interactive map surfaces are not indexed
    uniformly. 2GIS and Google Maps therefore also receive bounded direct public browser
    search tasks. Those tasks only navigate to public pages; factual coverage is granted
    solely from content that ARGUS actually fetches and stores as Evidence/Provenance.
    """

    version = "public-map-sources/7"
    supported_intents = frozenset(
        {
            "reviews",
            "comments",
            "complaints",
            "discussions",
        }
    )
    direct_navigation_version = "public-map-direct-navigation/2"

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

    @property
    def target_source_count(self) -> int:
        return self.target_sources_per_intent

    def direct_navigation_tasks(
        self,
        request: CollectionRequest,
        *,
        observations: list[Observation] | None = None,
        limit: int = 2,
    ) -> list[SourceTask]:
        """Return bounded direct browser entry points for under-indexed map providers."""

        if limit <= 0:
            return []
        navigation_goals = [
            intent for intent in request.intents if intent in self.supported_intents
        ]
        if not navigation_goals:
            return []
        anchors = self._anchors(request, observations or [])
        if not anchors:
            return []
        anchor = anchors[0]
        factual_goals = self._requested_intents(request)
        goal = (factual_goals or navigation_goals)[0]
        tasks: list[SourceTask] = []
        for profile in self.sources:
            url = self._direct_search_url(profile, anchor)
            if url is None:
                continue
            tasks.append(
                SourceTask(
                    source_id="generic_web",
                    goal=goal,
                    url=url,
                    depth=0,
                    task_key=f"public_map_direct:{profile.source_id}:{anchor.casefold()}",
                    metadata={
                        "public_map_direct_navigation": True,
                        "public_map_direct_navigation_version": self.direct_navigation_version,
                        "public_map_provider": profile.source_id,
                        "public_map_anchor": anchor,
                        "research_goals": list(dict.fromkeys(navigation_goals)),
                        "allowed_domains": list(request.constraints.allowed_domains),
                    },
                )
            )
            if len(tasks) >= limit:
                break
        return tasks

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
        suffix = self._suffix(
            remaining_intents,
            language,
            public_ugc_navigation=self._is_urban_signals(request),
        )

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
                "direct_navigation": item.direct_search_mode is not None,
            }
            for item in self.sources
        ]

    def _requested_intents(self, request: CollectionRequest) -> list[str]:
        requested = [
            intent
            for intent in request.intents
            if intent in self.supported_intents
        ]
        if self._is_urban_signals(request):
            requested = [intent for intent in requested if intent != "reviews"]
        return list(dict.fromkeys(requested))

    def _anchors(
        self,
        request: CollectionRequest,
        observations: list[Observation],
    ) -> list[str]:
        values: list[str] = []
        seen: set[str] = set()

        urban_signals = self._is_urban_signals(request)
        if urban_signals:
            city = (request.territory.city or "").strip()
            street_names: list[str] = []
            trusted_street = radius_street_text(request)
            if trusted_street:
                street_names.append(trusted_street)
            street_names.extend(
                nearby_radius_street_names(request, observations, limit=8)
            )
            for street_name in street_names:
                street_anchor = (
                    f"{city}, {street_name}" if city else street_name
                )
                key = street_anchor.casefold()
                if key in seen:
                    continue
                values.append(street_anchor)
                seen.add(key)

        territory = (
            radius_scope_text(request)
            if urban_signals
            else self._territory_text(request)
        )
        if territory and territory.casefold() not in seen:
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
    def _suffix(
        intents: list[str],
        language: str,
        *,
        public_ugc_navigation: bool = False,
    ) -> str:
        requested = set(intents)
        if language == "ru":
            terms: list[str] = []
            if public_ugc_navigation:
                terms.append("отзывы")
            if "reviews" in requested:
                terms.append("отзывы")
            if "comments" in requested:
                terms.append("комментарии")
            if "complaints" in requested:
                terms.append("жалобы")
            if "discussions" in requested:
                terms.append("обсуждения")
            return " ".join(dict.fromkeys(terms))[:160] or "отзывы комментарии"
        terms = []
        if public_ugc_navigation:
            terms.append("reviews")
        if "reviews" in requested:
            terms.append("reviews")
        if "comments" in requested:
            terms.append("comments")
        if "complaints" in requested:
            terms.append("complaints")
        if "discussions" in requested:
            terms.append("discussion")
        return " ".join(dict.fromkeys(terms))[:160] or "reviews comments"

    @staticmethod
    def _query(profile: PublicMapSourceProfile, anchor: str, suffix: str) -> str:
        return f'site:{profile.domain_scope} "{anchor}" {suffix}'[:512].rstrip()

    @staticmethod
    def _direct_search_url(profile: PublicMapSourceProfile, anchor: str) -> str | None:
        if profile.direct_search_mode == "2gis_path":
            return f"https://2gis.ru/search/{quote(anchor, safe='')}"
        if profile.direct_search_mode == "google_maps_url":
            return f"https://www.google.com/maps/search/?api=1&query={quote_plus(anchor)}"
        return None

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

    @staticmethod
    def _is_urban_signals(request: CollectionRequest) -> bool:
        pack = resolved_tool_pack_from_request(request)
        return pack is not None and pack.planner_policy == "urban_signals"