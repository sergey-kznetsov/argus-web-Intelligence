from __future__ import annotations

import json
from dataclasses import dataclass
from math import asin, cos, radians, sin, sqrt
import re

from argus.contracts.models import CollectionRequest, Observation, Point


@dataclass(frozen=True, slots=True)
class TerritoryRelevanceResult:
    matched: bool
    basis: str
    matched_anchors: tuple[str, ...] = ()
    distance_meters: float | None = None


class TerritoryRelevanceEvaluator:
    """Deterministically verify that a factual page belongs to the requested territory.

    Search queries and consumer metadata are intentionally ignored: they explain why ARGUS
    visited a URL, but do not prove that the fetched page is geographically relevant. The
    evaluator uses source-backed text/data or explicit source coordinates only.
    """

    version = "territory-relevance/4"
    point_tolerance_meters = 250
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

            # An address-scoped request must not be downgraded to a city-only match. A search
            # engine may return an otherwise unrelated page from the same city. House-number
            # matching is proximity-bound so an unrelated date like "27 июля" cannot attach a
            # street-level article to house 27.
            return TerritoryRelevanceResult(False, "address_anchor_missing")

        if city and city in haystack:
            return TerritoryRelevanceResult(True, "city_phrase", (city,))

        city_tokens = self.territory_tokens(city)
        matched_city = [token for token in city_tokens if self.contains_token(haystack, token)]
        if city_tokens and len(matched_city) >= min(2, len(city_tokens)):
            return TerritoryRelevanceResult(True, "city_tokens", tuple(matched_city[:3]))

        if request.territory.point is not None:
            return TerritoryRelevanceResult(False, "source_geo_missing")
        return TerritoryRelevanceResult(False, "territory_anchor_missing")

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
        """Return deterministic Latin aliases used only for navigation/relevance matching.

        These aliases never become factual evidence. They exist so a Cyrillic territory such
        as ``Пермь, Комсомольский`` can be matched against public URL slugs such as
        ``/perm/komsomolskiy-...`` without an external transliteration service.
        """

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

    @staticmethod
    def contains_token(haystack: str, token: str) -> bool:
        return re.search(rf"(?<!\w){re.escape(token)}(?!\w)", haystack) is not None

    @staticmethod
    def normalize_text(value: str) -> str:
        return " ".join(re.findall(r"[\w/-]+", value.casefold(), flags=re.UNICODE))

    @staticmethod
    def _is_address_number(token: str) -> bool:
        return re.fullmatch(r"\d+[a-zа-яё]?(?:[-/]\d+[a-zа-яё]?)?", token, flags=re.IGNORECASE) is not None

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
        allowed = float(request.territory.radius_meters or self.point_tolerance_meters)
        return TerritoryRelevanceResult(
            distance <= allowed,
            "source_geo_within_radius" if distance <= allowed else "source_geo_outside_radius",
            distance_meters=round(distance, 3),
        )

    @staticmethod
    def _distance_meters(left: Point, right: Point) -> float:
        earth_radius = 6_371_000.0
        lat1 = radians(left.latitude)
        lat2 = radians(right.latitude)
        delta_lat = radians(right.latitude - left.latitude)
        delta_lon = radians(right.longitude - left.longitude)
        haversine = sin(delta_lat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(delta_lon / 2) ** 2
        return 2 * earth_radius * asin(sqrt(haversine))
