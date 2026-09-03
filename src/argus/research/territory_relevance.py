from __future__ import annotations

import json
from dataclasses import dataclass
from math import asin, cos, radians, sin, sqrt
import re
from urllib.parse import unquote, urlsplit

from argus.contracts.models import CollectionRequest, Observation, Point
from argus.toolpacks import resolved_tool_pack_from_request


@dataclass(frozen=True, slots=True)
class TerritoryRelevanceResult:
    matched: bool
    basis: str
    matched_anchors: tuple[str, ...] = ()
    distance_meters: float | None = None


class TerritoryRelevanceEvaluator:
    """Deterministically verify that a factual page belongs to the requested territory.

    Search queries and consumer metadata are intentionally ignored as evidence. For an
    urban-signals tool pack, source-backed coordinates inside the radius and source-backed
    street mentions are accepted scopes. A nearby business or other POI is not, by itself,
    evidence that its reviews belong to Kraken's social-problem research domain.
    """

    version = "territory-relevance/7"
    navigation_ranking_version = "territory-url-ranking/2"
    point_tolerance_meters = 250
    urban_signals_default_radius_meters = 1_000
    max_address_anchor_distance_tokens = 8
    max_data_chars = 30_000
    max_tokens = 12

    _STOPWORDS = {
        "street",
        "st",
        "road",
        "rd",
        "avenue",
        "ave",
        "house",
        "building",
        "city",
        "ulitsa",
        "prospekt",
        "улица",
        "ул",
        "проспект",
        "пр",
        "пр-т",
        "дом",
        "д",
        "корпус",
        "корп",
        "строение",
        "стр",
        "переулок",
        "пер",
        "шоссе",
        "площадь",
        "пл",
        "набережная",
        "наб",
        "бульвар",
        "бул",
        "город",
        "г",
    }
    _CYRILLIC_TO_LATIN = {
        "а": "a",
        "б": "b",
        "в": "v",
        "г": "g",
        "д": "d",
        "е": "e",
        "ё": "yo",
        "ж": "zh",
        "з": "z",
        "и": "i",
        "й": "y",
        "к": "k",
        "л": "l",
        "м": "m",
        "н": "n",
        "о": "o",
        "п": "p",
        "р": "r",
        "с": "s",
        "т": "t",
        "у": "u",
        "ф": "f",
        "х": "kh",
        "ц": "ts",
        "ч": "ch",
        "ш": "sh",
        "щ": "shch",
        "ъ": "",
        "ы": "y",
        "ь": "",
        "э": "e",
        "ю": "yu",
        "я": "ya",
    }

    def matches(self, request: CollectionRequest, observation: Observation) -> bool:
        return self.evaluate(request, observation).matched

    def evaluate(
        self,
        request: CollectionRequest,
        observation: Observation,
    ) -> TerritoryRelevanceResult:
        point_match = self._point_match(request, observation)
        if point_match is not None:
            return point_match

        haystack = self.observation_text(observation)
        if not haystack:
            return TerritoryRelevanceResult(False, "empty_source_context")

        address = self.normalize_text(request.territory.address or "")
        city = self.normalize_text(request.territory.city or "")

        if address and address in haystack:
            return TerritoryRelevanceResult(True, "exact_address", (address,))

        if address:
            address_tokens = self.territory_tokens(address)
            city_tokens = set(self.territory_tokens(city)) if city else set()
            address_tokens = [token for token in address_tokens if token not in city_tokens]
            number_tokens = [token for token in address_tokens if self._is_address_number(token)]
            lexical_tokens = [token for token in address_tokens if token not in number_tokens]
            nearby = self._nearby_address_anchors(haystack, lexical_tokens, number_tokens)

            if nearby:
                return TerritoryRelevanceResult(True, "street_and_house_number", nearby)

            latin_aliases = self.latin_address_aliases(lexical_tokens)
            nearby_latin = self._nearby_address_anchors(
                haystack,
                latin_aliases,
                number_tokens,
            )
            if nearby_latin:
                return TerritoryRelevanceResult(
                    True,
                    "street_and_house_number_transliterated",
                    nearby_latin,
                )

            if not number_tokens:
                matched_lexical = [
                    token for token in lexical_tokens if self.contains_token(haystack, token)
                ]
                if len(matched_lexical) >= min(2, len(lexical_tokens)):
                    return TerritoryRelevanceResult(
                        True,
                        "address_tokens",
                        tuple(matched_lexical[:3]),
                    )

            street_match = self._urban_signal_street_match(request, haystack, city)
            if street_match is not None:
                return street_match

            return TerritoryRelevanceResult(False, "address_anchor_missing")

        if city and city in haystack:
            return TerritoryRelevanceResult(True, "city_phrase", (city,))

        matched_city = self._matched_city_anchors(haystack, city)
        city_tokens = self.territory_tokens(city)
        if city_tokens and len(matched_city) >= min(2, len(city_tokens)):
            return TerritoryRelevanceResult(True, "city_tokens", tuple(matched_city[:3]))
        if len(city_tokens) == 1 and matched_city:
            return TerritoryRelevanceResult(True, "city_inflected_token", tuple(matched_city[:1]))

        if request.territory.point is not None:
            return TerritoryRelevanceResult(False, "source_geo_missing")
        return TerritoryRelevanceResult(False, "territory_anchor_missing")

    def _urban_signal_street_match(
        self,
        request: CollectionRequest,
        haystack: str,
        city: str,
    ) -> TerritoryRelevanceResult | None:
        if self._planner_policy(request) != "urban_signals":
            return None
        raw_street = request.territory.metadata.get("street")
        if not isinstance(raw_street, str) or not raw_street.strip():
            return None
        street = self.normalize_text(raw_street)
        street_tokens = self.territory_tokens(street)
        if not street_tokens:
            return None
        matched_street = [
            token for token in street_tokens if self.contains_token(haystack, token)
        ]
        if len(matched_street) < min(2, len(street_tokens)):
            return None
        if not self._city_matches(haystack, city):
            return None
        anchors = tuple([*matched_street[:2], *self.territory_tokens(city)[:1]])
        return TerritoryRelevanceResult(True, "urban_signal_street_scope", anchors)

    def navigation_url_score(self, url: str, request: CollectionRequest) -> float:
        """Score a URL only for navigation ordering; the score is never factual evidence."""

        path = unquote(urlsplit(str(url)).path).casefold()
        url_tokens = [
            token
            for token in re.findall(r"[a-zа-яё0-9]+", path, flags=re.UNICODE)
            if token
        ]
        if not url_tokens:
            return 0.0

        city_tokens = self.territory_tokens(request.territory.city or "")
        city_aliases = self._navigation_aliases(city_tokens)

        address_tokens = self.territory_tokens(request.territory.address or "")
        city_set = set(city_tokens)
        address_tokens = [token for token in address_tokens if token not in city_set]
        number_tokens = [token for token in address_tokens if self._is_address_number(token)]
        lexical_tokens = [token for token in address_tokens if token not in number_tokens]
        address_aliases = self._navigation_aliases(lexical_tokens)

        city_exact = self._navigation_match_count(city_aliases, url_tokens, prefix=False)
        city_with_prefix = self._navigation_match_count(city_aliases, url_tokens, prefix=True)
        city_prefix_only = max(0, city_with_prefix - city_exact)
        address_matches = self._navigation_match_count(address_aliases, url_tokens, prefix=True)
        number_matches = self._navigation_match_count(number_tokens, url_tokens, prefix=False)

        score = float(city_exact * 400 + city_prefix_only * 60 + address_matches * 40)
        if city_exact:
            score += 400.0
        elif city_prefix_only:
            score += 40.0
        if city_exact and address_matches:
            score += 200.0
        if city_exact and address_matches and number_matches:
            score += 100.0
        elif number_matches and (city_exact or address_matches):
            score += 10.0
        elif number_matches:
            score += 1.0

        if city_exact or city_prefix_only or address_matches:
            segment_count = len([segment for segment in path.split("/") if segment])
            score += float(max(0, 4 - min(segment_count, 4)) * 5)
        return score

    def observation_text(self, observation: Observation) -> str:
        parts = [observation.title or "", observation.text or ""]
        try:
            data = json.dumps(
                observation.data,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
        except (TypeError, ValueError):
            data = ""
        parts.append(data[: self.max_data_chars])
        return self.normalize_text(" ".join(parts))

    def territory_tokens(self, value: str) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for token in re.findall(r"[\w/-]+", value.casefold(), flags=re.UNICODE):
            token = token.strip("-/")
            if not token or token in self._STOPWORDS:
                continue
            if not self._is_address_number(token) and len(token) < 3:
                continue
            if token in seen:
                continue
            seen.add(token)
            result.append(token)
            if len(result) >= self.max_tokens:
                break
        return result

    def latin_address_aliases(self, lexical_tokens: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for token in lexical_tokens:
            if re.search(r"[а-яё]", token, flags=re.IGNORECASE) is None:
                continue
            transliterated = "".join(
                self._CYRILLIC_TO_LATIN.get(char, char)
                for char in token.casefold()
            )
            variants = [transliterated]
            if transliterated.endswith("skiy"):
                variants.append(f"{transliterated[:-4]}sky")
            if transliterated.endswith("iy"):
                variants.append(f"{transliterated[:-2]}y")
            if transliterated.endswith("yy"):
                variants.append(f"{transliterated[:-2]}y")
            for value in variants:
                if len(value) < 3 or value in seen:
                    continue
                seen.add(value)
                result.append(value)
        return result

    def _navigation_aliases(self, tokens: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for value in [*tokens, *self.latin_address_aliases(tokens)]:
            normalized = value.casefold().strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            result.append(normalized)
        return result

    @staticmethod
    def _navigation_match_count(
        anchors: list[str],
        url_tokens: list[str],
        *,
        prefix: bool,
    ) -> int:
        matched = 0
        for anchor in anchors:
            if any(token == anchor for token in url_tokens):
                matched += 1
                continue
            if prefix and len(anchor) >= 4 and any(token.startswith(anchor) for token in url_tokens):
                matched += 1
        return matched

    def _nearby_address_anchors(
        self,
        haystack: str,
        lexical_tokens: list[str],
        number_tokens: list[str],
    ) -> tuple[str, ...]:
        if not lexical_tokens or not number_tokens:
            return ()
        source_tokens = re.findall(r"[\w/-]+", haystack, flags=re.UNICODE)
        lexical_positions: list[tuple[int, str]] = []
        number_positions: list[tuple[int, str]] = []
        lexical_set = set(lexical_tokens)
        number_set = set(number_tokens)
        for index, raw in enumerate(source_tokens):
            token = raw.strip("-/")
            if token in lexical_set:
                lexical_positions.append((index, token))
            if token in number_set:
                number_positions.append((index, token))
        for lexical_index, lexical in lexical_positions:
            for number_index, number in number_positions:
                if abs(lexical_index - number_index) <= self.max_address_anchor_distance_tokens:
                    return (lexical, number)
        return ()

    def _matched_city_anchors(self, haystack: str, city: str) -> list[str]:
        matched: list[str] = []
        for token in self.territory_tokens(city):
            aliases = self._russian_city_case_aliases(token)
            found = next(
                (alias for alias in aliases if self.contains_token(haystack, alias)),
                None,
            )
            if found is not None:
                matched.append(found)
        return matched

    @staticmethod
    def _russian_city_case_aliases(token: str) -> tuple[str, ...]:
        """Return conservative exact-token Russian locative aliases for a city name.

        These aliases are navigation-independent source-text anchors, not fuzzy matching.
        They cover common forms such as ``Пермь -> Перми``, ``Ижевск -> Ижевске`` and
        ``Москва -> Москве`` without accepting arbitrary prefixes or edit distance matches.
        """

        value = token.casefold().strip()
        if re.fullmatch(r"[а-яё]{3,}", value) is None:
            return (value,)
        aliases = [value]
        if value.endswith("ь") and len(value) > 3:
            aliases.append(f"{value[:-1]}и")
        elif value.endswith("а") and len(value) > 3:
            aliases.append(f"{value[:-1]}е")
        elif value.endswith("я") and len(value) > 3:
            aliases.append(f"{value[:-1]}е")
        elif value[-1] not in "аеёиоуыэюяйьъ":
            aliases.append(f"{value}е")
        return tuple(dict.fromkeys(aliases))

    @staticmethod
    def contains_token(haystack: str, token: str) -> bool:
        return re.search(rf"(?<!\w){re.escape(token)}(?!\w)", haystack) is not None

    @staticmethod
    def normalize_text(value: str) -> str:
        return " ".join(re.findall(r"[\w/-]+", value.casefold(), flags=re.UNICODE))

    @staticmethod
    def _is_address_number(token: str) -> bool:
        return re.fullmatch(
            r"\d+[a-zа-яё]?(?:[-/]\d+[a-zа-яё]?)?",
            token,
            flags=re.IGNORECASE,
        ) is not None

    def _point_match(
        self,
        request: CollectionRequest,
        observation: Observation,
    ) -> TerritoryRelevanceResult | None:
        requested = request.territory.point
        observed = observation.geo
        if requested is None or observed is None:
            return None
        distance = self._distance_meters(requested, observed)
        configured_radius = request.territory.radius_meters
        if configured_radius is not None:
            allowed = float(configured_radius)
        elif self._planner_policy(request) == "urban_signals":
            allowed = float(self.urban_signals_default_radius_meters)
        else:
            allowed = float(self.point_tolerance_meters)
        return TerritoryRelevanceResult(
            distance <= allowed,
            "source_geo_within_radius" if distance <= allowed else "source_geo_outside_radius",
            distance_meters=round(distance, 3),
        )

    def _city_matches(self, haystack: str, city: str) -> bool:
        if not city:
            return True
        if city in haystack:
            return True
        city_tokens = self.territory_tokens(city)
        matched = self._matched_city_anchors(haystack, city)
        return bool(city_tokens) and len(matched) >= min(2, len(city_tokens))

    @staticmethod
    def _planner_policy(request: CollectionRequest) -> str:
        pack = resolved_tool_pack_from_request(request)
        return pack.planner_policy if pack is not None else "universal"

    @staticmethod
    def _distance_meters(left: Point, right: Point) -> float:
        earth_radius = 6_371_000.0
        lat1 = radians(left.latitude)
        lat2 = radians(right.latitude)
        delta_lat = radians(right.latitude - left.latitude)
        delta_lon = radians(right.longitude - left.longitude)
        haversine = sin(delta_lat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(delta_lon / 2) ** 2
        return 2 * earth_radius * asin(sqrt(haversine))
