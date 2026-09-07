from __future__ import annotations

from dataclasses import dataclass

from argus.contracts.models import CollectionRequest, Observation
from argus.research.entities import AreaEntityResearchPlanner
from argus.research.followup import FollowupPlan, FollowupResearchPlanner
from argus.research.planner import ResearchPlan, ResearchPlanner


def exact_territory_text(request: CollectionRequest) -> str:
    city = (request.territory.city or "").strip()
    address = (request.territory.address or "").strip()
    if city and address:
        return address if city.casefold() in address.casefold() else f"{city}, {address}"
    if address:
        return address
    if city:
        return city
    if request.territory.point is not None:
        return (
            f"{request.territory.point.latitude:.6f},"
            f"{request.territory.point.longitude:.6f}"
        )
    return "location"


def radius_scope_text(
    request: CollectionRequest,
    *,
    entity_scope: bool = False,
) -> str:
    """Return a search anchor that represents an area instead of one house.

    Search engines do not implement a trustworthy ``within N metres`` operator. For an
    actual point+radius request ARGUS therefore broadens textual discovery while the
    geometric boundary stays authoritative in source-backed geo validation. Initial
    discovery uses city+street where available; follow-up research for a nearby named
    entity uses the city so side streets inside the circle are not accidentally excluded.
    """

    territory = request.territory
    if territory.point is None or territory.radius_meters is None:
        return exact_territory_text(request)

    city = (territory.city or "").strip()
    street_raw = territory.metadata.get("street")
    street = " ".join(street_raw.split()).strip() if isinstance(street_raw, str) else ""

    if entity_scope and city:
        return city
    if street:
        return f"{city}, {street}" if city else street
    if city:
        return city
    return exact_territory_text(request)


def radius_scoped_request(
    request: CollectionRequest,
    *,
    entity_scope: bool = False,
) -> CollectionRequest:
    scope = radius_scope_text(request, entity_scope=entity_scope)
    exact = exact_territory_text(request)
    if scope.casefold() == exact.casefold():
        return request

    territory = request.territory.model_copy(update={"address": scope})
    return request.model_copy(update={"territory": territory})


@dataclass(slots=True)
class RadiusAwareResearchPlanner:
    delegate: ResearchPlanner

    async def plan(self, request: CollectionRequest) -> ResearchPlan:
        scoped = radius_scoped_request(request)
        plan = await self.delegate.plan(scoped)
        if scoped is not request:
            plan.notes.append(
                "radius_scope=textual_area_seed;"
                f"radius_meters={request.territory.radius_meters};"
                "geometric_validation=source_geo"
            )
        return plan


@dataclass(slots=True)
class RadiusAwareFollowupResearchPlanner:
    delegate: FollowupResearchPlanner

    async def plan_followups(
        self,
        request: CollectionRequest,
        observations: list[Observation],
        *,
        seen_queries: set[str],
        max_queries: int,
    ) -> FollowupPlan:
        scoped = radius_scoped_request(request)
        plan = await self.delegate.plan_followups(
            scoped,
            observations,
            seen_queries=seen_queries,
            max_queries=max_queries,
        )
        if scoped is not request:
            plan.notes.append(
                "radius_scope=followup_area_seed;"
                f"radius_meters={request.territory.radius_meters}"
            )
        return plan


@dataclass(slots=True)
class RadiusAwareAreaEntityResearchPlanner:
    delegate: AreaEntityResearchPlanner

    @property
    def area_intents(self) -> set[str]:
        return self.delegate.area_intents

    def expand(
        self,
        request: CollectionRequest,
        observations,
        *,
        seen_queries: set[str],
        limit: int | None = None,
    ) -> list[str]:
        scoped = radius_scoped_request(request, entity_scope=True)
        return self.delegate.expand(
            scoped,
            observations,
            seen_queries=seen_queries,
            limit=limit,
        )
