from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from argus.contracts.models import CollectionRequest, Observation
from argus.research.radius_scope import (
    nearby_radius_street_names,
    radius_scope_text,
    radius_street_text,
)


_CURATED_NON_OFFICIAL_ROOTS = (
    "2gis.ru",
    "flamp.ru",
    "google.com",
    "orgpage.ru",
    "rubrikator.org",
    "spravker.ru",
    "yandex.com",
    "yandex.ru",
    "yell.ru",
    "zoon.ru",
)


@dataclass(frozen=True, slots=True)
class SourceContourProfile:
    contour_id: str
    priority: int
    max_destinations: int
    ru_templates: tuple[str, ...]
    en_templates: tuple[str, ...]
    description: str
    denied_domain_roots: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SourceContourPlan:
    contour_id: str
    priority: int
    queries: tuple[str, ...]
    max_destinations: int
    description: str
    denied_domain_roots: tuple[str, ...] = ()
    street_names: tuple[str, ...] = ()


URBAN_SIGNAL_SOURCE_CONTOURS: tuple[SourceContourProfile, ...] = (
    SourceContourProfile(
        contour_id="official_government",
        priority=10,
        max_destinations=3,
        ru_templates=(
            '"{anchor}" "{region}" администрация официальный сайт благоустройство',
            '"{anchor}" "{region}" правительство министерство официальный сайт',
            '"{city}" "{street}" "{region}" официальный муниципальный портал',
        ),
        en_templates=(
            '"{anchor}" "{region}" official municipality government public works',
            '"{anchor}" "{region}" official regional government ministry',
            '"{city}" "{street}" "{region}" official municipal portal',
        ),
        description="Federal, regional and municipal official public-web sources.",
        denied_domain_roots=_CURATED_NON_OFFICIAL_ROOTS,
    ),
    SourceContourProfile(
        contour_id="public_appeals",
        priority=20,
        max_destinations=3,
        ru_templates=(
            '"{anchor}" "{region}" обращения граждан жалоба официальный',
            '"{anchor}" "{region}" интернет-приемная обращение жителей',
            '"{city}" "{street}" "{region}" общественная приемная обращение',
        ),
        en_templates=(
            '"{anchor}" "{region}" official citizen appeals complaints public requests',
            '"{anchor}" "{region}" public reception resident complaint',
            '"{city}" "{street}" "{region}" public appeal reception',
        ),
        description="Public citizen-appeal and municipal feedback surfaces.",
        denied_domain_roots=_CURATED_NON_OFFICIAL_ROOTS,
    ),
    SourceContourProfile(
        contour_id="housing_utilities",
        priority=30,
        max_destinations=3,
        ru_templates=(
            '"{anchor}" "{region}" ЖКХ управляющая компания жилищная инспекция',
            'site:dom.gosuslugi.ru "{city}" "{street}"',
            'site:dom.mingkh.ru "{city}" "{street}"',
        ),
        en_templates=(
            '"{anchor}" "{region}" housing utilities management company inspection',
            'site:dom.gosuslugi.ru "{city}" "{street}"',
            'site:dom.mingkh.ru "{city}" "{street}"',
        ),
        description="Public housing, utilities, inspection and residential-management sources.",
        denied_domain_roots=_CURATED_NON_OFFICIAL_ROOTS,
    ),
    SourceContourProfile(
        contour_id="local_forums",
        priority=40,
        max_destinations=2,
        ru_templates=(
            '"{anchor}" "{region}" форум жители обсуждение',
            '"{city}" "{street}" городской форум',
        ),
        en_templates=(
            '"{anchor}" "{region}" local forum residents discussion',
            '"{city}" "{street}" city forum',
        ),
        description="Local forums and resident discussion boards.",
    ),
    SourceContourProfile(
        contour_id="local_media",
        priority=50,
        max_destinations=2,
        ru_templates=(
            '"{anchor}" "{region}" новости происшествие авария ремонт конфликт',
            '"{anchor}" "{region}" местные СМИ новости',
        ),
        en_templates=(
            '"{anchor}" "{region}" local news incident accident repair conflict',
            '"{anchor}" "{region}" local media news',
        ),
        description="Local news and incident reporting.",
    ),
    SourceContourProfile(
        contour_id="public_communities",
        priority=60,
        max_destinations=1,
        ru_templates=(
            '"{anchor}" "{region}" жители сообщество район обсуждение',
        ),
        en_templates=(
            '"{anchor}" "{region}" residents community neighborhood discussion',
        ),
        description="Publicly accessible local resident communities.",
    ),
    SourceContourProfile(
        contour_id="general_web",
        priority=70,
        max_destinations=1,
        ru_templates=(
            '"{anchor}" "{region}" жалобы проблемы жители происшествия обсуждения',
        ),
        en_templates=(
            '"{anchor}" "{region}" complaints resident problems incidents discussion',
        ),
        description="Open-web catch-all lane for sources outside curated classes.",
    ),
)


class SourceContourResearchPlanner:
    """Build independent public-source discovery lanes for a planner policy.

    Source contours are navigation policy, not domain interpretation. They ensure that one
    highly ranked source class (for example map reviews) cannot monopolize discovery. Actual
    facts must still be fetched, normalized and backed by Evidence/Provenance.
    """

    version = "source-contours/5"

    def __init__(
        self,
        *,
        policy_profiles: dict[str, tuple[SourceContourProfile, ...]] | None = None,
        max_query_chars: int = 512,
        max_nearby_streets: int | None = None,
    ) -> None:
        self.policy_profiles = policy_profiles or {
            "urban_signals": URBAN_SIGNAL_SOURCE_CONTOURS,
        }
        self.max_query_chars = max(64, int(max_query_chars))
        self.max_nearby_streets = (
            None
            if max_nearby_streets is None
            else max(1, int(max_nearby_streets))
        )

    def supports_policy(self, planner_policy: str) -> bool:
        return planner_policy in self.policy_profiles

    def plans(
        self,
        request: CollectionRequest,
        *,
        planner_policy: str,
        observations: Iterable[Observation] = (),
    ) -> list[SourceContourPlan]:
        profiles = self.policy_profiles.get(planner_policy, ())
        if not profiles:
            return []
        anchor = radius_scope_text(request)
        city = (request.territory.city or "").strip() or anchor
        street = radius_street_text(request) or city
        region_raw = request.territory.metadata.get("region")
        region = (
            " ".join(region_raw.split()).strip()
            if isinstance(region_raw, str) and region_raw.strip()
            else city
        )
        nearby_streets = nearby_radius_street_names(
            request,
            observations,
            limit=self.max_nearby_streets,
        )
        language = self._language(request, anchor)
        plans: list[SourceContourPlan] = []
        for profile in sorted(profiles, key=lambda item: (item.priority, item.contour_id)):
            templates = profile.ru_templates if language == "ru" else profile.en_templates
            query_values: list[str] = []
            for index, nearby_street in enumerate(nearby_streets):
                street_anchor = (
                    f"{city}, {nearby_street}" if city else nearby_street
                )
                template = templates[index % len(templates)]
                query_values.append(
                    self._bounded_query(
                        template.format(
                            anchor=street_anchor,
                            city=city,
                            street=nearby_street,
                            region=region,
                        )
                    )
                )
            query_values.extend(
                self._bounded_query(
                    template.format(
                        anchor=anchor,
                        city=city,
                        street=street,
                        region=region,
                    )
                )
                for template in templates
            )
            queries = tuple(dict.fromkeys(query for query in query_values if query))
            if not queries:
                continue
            plans.append(
                SourceContourPlan(
                    contour_id=profile.contour_id,
                    priority=profile.priority,
                    queries=queries,
                    max_destinations=max(
                        profile.max_destinations,
                        len(queries),
                    ),
                    description=profile.description,
                    denied_domain_roots=profile.denied_domain_roots,
                    street_names=tuple(nearby_streets),
                )
            )
        return plans

    def catalog(self, planner_policy: str) -> list[dict[str, object]]:
        return [
            {
                "contour_id": profile.contour_id,
                "priority": profile.priority,
                "max_destinations": profile.max_destinations,
                "description": profile.description,
                "denied_domain_roots": list(profile.denied_domain_roots),
            }
            for profile in sorted(
                self.policy_profiles.get(planner_policy, ()),
                key=lambda item: (item.priority, item.contour_id),
            )
        ]

    def _bounded_query(self, value: str) -> str:
        return " ".join(value.split()).strip()[: self.max_query_chars].rstrip()

    @staticmethod
    def _language(request: CollectionRequest, anchor: str) -> str:
        configured = (request.constraints.language or "").casefold()
        if configured.startswith("ru"):
            return "ru"
        if configured.startswith("en"):
            return "en"
        return (
            "ru"
            if any("а" <= char.casefold() <= "я" or char.casefold() == "ё" for char in anchor)
            else "en"
        )
