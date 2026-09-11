from __future__ import annotations

from dataclasses import dataclass
from math import asin, cos, radians, sin, sqrt
from typing import TYPE_CHECKING, Any, Iterable

from argus.contracts.models import CollectionRequest, Observation

if TYPE_CHECKING:
    from argus.research.entities import AreaEntityResearchPlanner
    from argus.research.followup import FollowupPlan, FollowupResearchPlanner
    from argus.research.planner import ResearchPlan, ResearchPlanner

_TRUSTED_STREET_PRECISIONS = frozenset(
    {
        "address",
        "building",
        "house",
        "intersection",
        "street",
    }
)


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


def radius_street_text(request: CollectionRequest) -> str | None:
    """Return a street anchor only when its transport metadata is trustworthy.

    A POI/name geocoder result can populate a field called ``street`` with the POI label
    rather than an actual street. For a point+radius collection that false precision is
    worse than city-level discovery. A street is therefore retained when the transport
    also supplies a house or an explicit street/address precision. Non-radius requests keep
    their supplied street because exact-address semantics are intentional there.
    """

    raw = request.territory.metadata.get("street")
    street = " ".join(raw.split()).strip() if isinstance(raw, str) else ""
    if not street:
        return None

    territory = request.territory
    if territory.point is None or territory.radius_meters is None:
        return street

    house_raw = territory.metadata.get("house")
    house = " ".join(house_raw.split()).strip() if isinstance(house_raw, str) else ""
    if house:
        return street

    precision_raw = territory.metadata.get("precision")
    precision = (
        " ".join(precision_raw.split()).strip().casefold()
        if isinstance(precision_raw, str)
        else ""
    )
    if precision in _TRUSTED_STREET_PRECISIONS:
        return street
    return None


def _point_distance_meters(first, second) -> float:
    """Return great-circle distance in metres for deterministic street ordering."""

    first_lat = radians(first.latitude)
    second_lat = radians(second.latitude)
    latitude_delta = second_lat - first_lat
    longitude_delta = radians(second.longitude - first.longitude)
    haversine = (
        sin(latitude_delta / 2) ** 2
        + cos(first_lat) * cos(second_lat) * sin(longitude_delta / 2) ** 2
    )
    return 2 * 6_371_008.8 * asin(sqrt(min(1.0, haversine)))


def nearby_radius_street_inventory(
    request: CollectionRequest,
    observations: Iterable[Observation],
    *,
    limit: int | None = 8,
) -> list[dict[str, Any]]:
    """Return evidence-backed OSM street records admitted by the radius inventory.

    The Overpass around query is the inclusion boundary. A returned way can intersect
    the circle while its representative centre lies outside it, so coordinates are used
    only to order streets by proximity and never to reject an admitted way.

    ``limit=None`` is the completeness mode used by mandatory urban research. It preserves
    every distinct named street returned by the bounded radius inventory instead of silently
    truncating the territory to the historical eight-street convenience limit.
    """

    territory = request.territory
    if (
        (limit is not None and limit <= 0)
        or territory.point is None
        or territory.radius_meters is None
    ):
        return []

    candidates: list[tuple[float, str, dict[str, Any]]] = []
    seen: set[str] = set()
    for observation in observations:
        if (
            observation.source != "openstreetmap_overpass"
            or observation.source_kind != "map_place"
        ):
            continue
        categories = observation.data.get("categories")
        if not isinstance(categories, (list, tuple, set)) or not any(
            isinstance(value, str) and value.casefold().startswith("highway:")
            for value in categories
        ):
            continue
        raw_name = observation.data.get("name") or observation.title
        if not isinstance(raw_name, str):
            continue
        name = " ".join(raw_name.replace('"', " ").split()).strip()
        key = name.casefold()
        if len(name) < 3 or key in seen:
            continue
        seen.add(key)
        distance = (
            _point_distance_meters(territory.point, observation.geo)
            if observation.geo is not None
            else float("inf")
        )
        candidates.append(
            (
                distance,
                key,
                {
                    "name": name,
                    "distance_meters": (
                        round(distance, 3) if distance != float("inf") else None
                    ),
                    "relationship": "returned_by_radius_inventory",
                    "provider": observation.source,
                    "observation_id": observation.observation_id,
                    "source_url": observation.url,
                    "geometry_basis": (
                        "representative_point_ordering_only"
                        if observation.geo is not None
                        else "overpass_radius_membership"
                    ),
                },
            )
        )

    candidates.sort(key=lambda item: (item[0], item[1]))
    rows = [row for _, _, row in candidates]
    return rows if limit is None else rows[:limit]


def nearby_radius_street_names(
    request: CollectionRequest,
    observations: Iterable[Observation],
    *,
    limit: int | None = 8,
) -> list[str]:
    """Return the stable name-only compatibility view of the street inventory."""

    return [
        str(item["name"])
        for item in nearby_radius_street_inventory(
            request,
            observations,
            limit=limit,
        )
    ]


def radius_scope_text(
    request: CollectionRequest,
    *,
    entity_scope: bool = False,
) -> str:
    """Return a search anchor that represents an area instead of one house.

    Search engines do not implement a trustworthy ``within N metres`` operator. For an
    actual point+radius request ARGUS therefore broadens textual discovery while the
    geometric boundary stays authoritative in source-backed geo validation. Initial
    discovery uses a verified city+street anchor where available; follow-up research for a
    nearby named entity uses the city so side streets inside the circle are not accidentally
    excluded.
    """

    territory = request.territory
    if territory.point is None or territory.radius_meters is None:
        return exact_territory_text(request)

    city = (territory.city or "").strip()
    street = radius_street_text(request) or ""

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
