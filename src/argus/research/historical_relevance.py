from __future__ import annotations

import re

from argus.contracts.models import CollectionRequest, Observation
from argus.research.territory_relevance import (
    TerritoryRelevanceEvaluator,
    TerritoryRelevanceResult,
)


class HistoricalTerritoryRelevanceEvaluator:
    """Allow bounded containing-street context only for historical research.

    Current facts remain address-strict. Historical sources, however, often document the
    containing street/prospect rather than repeating every modern house number. This evaluator
    first delegates to the normal strict territory matcher and only then permits a deterministic
    street-context fallback when source text independently contains the requested city, street
    lexical anchor and compatible street type. Search/navigation metadata is never consulted.
    """

    version = "historical-territory-relevance/2"
    min_anchor_root_chars = 5
    max_source_tokens = 12_000

    _STREET_TYPES: dict[str, tuple[str, ...]] = {
        "avenue": (
            "проспект",
            "проспекта",
            "проспекте",
            "проспекту",
            "проспектом",
            "prospekt",
            "avenue",
            "ave",
        ),
        "street": (
            "улица",
            "улицы",
            "улице",
            "улицу",
            "улицей",
            "ulitsa",
            "street",
            "st",
        ),
        "lane": (
            "переулок",
            "переулка",
            "переулке",
            "переулку",
            "переулком",
            "pereulok",
            "lane",
            "ln",
        ),
        "boulevard": (
            "бульвар",
            "бульвара",
            "бульваре",
            "бульвару",
            "бульваром",
            "bulvar",
            "boulevard",
            "blvd",
        ),
        "embankment": (
            "набережная",
            "набережной",
            "набережную",
            "naberezhnaya",
            "embankment",
        ),
        "square": (
            "площадь",
            "площади",
            "площадью",
            "ploshchad",
            "square",
            "sq",
        ),
        "highway": (
            "шоссе",
            "shosse",
            "highway",
            "hwy",
        ),
    }

    _RU_ADJECTIVE_ENDINGS = (
        "ского",
        "скому",
        "скими",
        "ских",
        "ском",
        "ская",
        "скую",
        "ской",
        "ские",
        "ский",
        "ское",
        "ого",
        "ому",
        "ыми",
        "ых",
        "ым",
        "ом",
        "ая",
        "ую",
        "ой",
        "его",
        "ему",
        "ими",
        "их",
        "ий",
        "им",
        "ем",
        "ое",
        "ые",
        "ый",
    )
    _LATIN_ADJECTIVE_ENDINGS = (
        "skogo",
        "skomu",
        "skom",
        "skiy",
        "sky",
        "skaya",
        "skoy",
        "aya",
        "ogo",
        "omu",
    )

    def __init__(self, base: TerritoryRelevanceEvaluator | None = None) -> None:
        self.base = base or TerritoryRelevanceEvaluator()

    def evaluate(
        self,
        request: CollectionRequest,
        observation: Observation,
    ) -> TerritoryRelevanceResult:
        strict = self.base.evaluate(request, observation)
        if strict.matched:
            return strict
        if strict.basis != "address_anchor_missing":
            return strict

        address = self.base.normalize_text(request.territory.address or "")
        city = self.base.normalize_text(request.territory.city or "")
        if not address or not city:
            return strict

        requested_type = self._street_type(address)
        if requested_type is None:
            return strict
        lexical = [
            token
            for token in self.base.territory_tokens(address)
            if not self.base._is_address_number(token)
        ]
        city_tokens = set(self.base.territory_tokens(city))
        lexical = [token for token in lexical if token not in city_tokens]
        anchor_roots = self._anchor_roots(lexical)
        if not anchor_roots:
            return strict

        haystack = self.base.observation_text(observation)
        source_tokens = re.findall(r"[\w/-]+", haystack, flags=re.UNICODE)[
            : self.max_source_tokens
        ]
        source_roots = {self._root(token) for token in source_tokens if token}
        source_roots.discard("")
        matched_root = next((root for root in anchor_roots if root in source_roots), None)
        if matched_root is None:
            return strict

        if not self._source_has_street_type(source_tokens, requested_type):
            return strict
        city_root = self._city_root(city)
        if not city_root or not any(
            self._prefix_match(city_root, self._root(token), minimum=4)
            for token in source_tokens
        ):
            return strict

        return TerritoryRelevanceResult(
            True,
            "historical_street_context",
            (matched_root, requested_type, city_root),
        )

    def _street_type(self, address: str) -> str | None:
        tokens = set(re.findall(r"[\w/-]+", address, flags=re.UNICODE))
        for canonical, aliases in self._STREET_TYPES.items():
            if any(alias in tokens for alias in aliases):
                return canonical
        return None

    def _anchor_roots(self, lexical: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for token in lexical:
            root = self._root(token)
            if len(root) >= self.min_anchor_root_chars and root not in seen:
                seen.add(root)
                result.append(root)
            for alias in self.base.latin_address_aliases([token]):
                alias_root = self._root(alias)
                if len(alias_root) >= self.min_anchor_root_chars and alias_root not in seen:
                    seen.add(alias_root)
                    result.append(alias_root)
        return result

    def _source_has_street_type(self, source_tokens: list[str], canonical: str) -> bool:
        aliases = self._STREET_TYPES[canonical]
        normalized = {token.casefold().strip("-/") for token in source_tokens}
        return any(alias in normalized for alias in aliases)

    def _city_root(self, city: str) -> str:
        tokens = self.base.territory_tokens(city)
        if not tokens:
            tokens = re.findall(r"[\w/-]+", city, flags=re.UNICODE)
        return self._root(tokens[0]) if tokens else ""

    def _root(self, raw: str) -> str:
        token = raw.casefold().strip("-/")
        if not token:
            return ""
        endings = (
            self._RU_ADJECTIVE_ENDINGS
            if re.search(r"[а-яё]", token, flags=re.IGNORECASE)
            else self._LATIN_ADJECTIVE_ENDINGS
        )
        for ending in endings:
            if token.endswith(ending) and len(token) - len(ending) >= self.min_anchor_root_chars:
                return token[: -len(ending)]
        if len(token) >= 5 and token.endswith("ь"):
            return token[:-1]
        return token

    @staticmethod
    def _prefix_match(left: str, right: str, *, minimum: int) -> bool:
        if len(left) < minimum or len(right) < minimum:
            return False
        return left.startswith(right) or right.startswith(left)
