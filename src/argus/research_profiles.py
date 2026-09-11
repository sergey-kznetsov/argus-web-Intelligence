from __future__ import annotations

from dataclasses import dataclass


class ResearchProfileContractError(ValueError):
    """Raised when a declarative research profile cannot be resolved safely."""


@dataclass(frozen=True, slots=True)
class ResearchCapability:
    """Reusable acquisition capability available to any analytical consumer."""

    capability_id: str
    source_family_ids: tuple[str, ...] = ()
    public_map_ids: tuple[str, ...] = ()
    required_source_ids: tuple[str, ...] = ()
    street_inventory: bool = False
    public_map_street_scope: bool = False
    radius_scope_queries: bool = False
    public_ugc_navigation: bool = False
    area_entity_mode: str = "verified_entities"
    description: str = ""


@dataclass(frozen=True, slots=True)
class CompletionPolicy:
    """Data-only completion and budget policy for one research profile."""

    mandatory_source_families: bool = False
    mandatory_public_maps: bool = False
    strict_sequential: bool = False
    skip_generic_discovery_after_mandatory: bool = False
    emergency_max_pages: int = 500
    emergency_max_duration_seconds: float = 7_200.0
    optional_pages: int = 24
    optional_duration_seconds: float = 120.0

    @property
    def has_mandatory_lanes(self) -> bool:
        return self.mandatory_source_families or self.mandatory_public_maps


@dataclass(frozen=True, slots=True)
class ResearchProfile:
    """A named composition of reusable capabilities, never a module identity."""

    profile_id: str
    version: int
    capability_ids: tuple[str, ...]
    completion_policy: CompletionPolicy = CompletionPolicy()
    description: str = ""


@dataclass(frozen=True, slots=True)
class ResolvedResearchProfile:
    profile_id: str
    version: int
    capability_ids: tuple[str, ...]
    source_family_ids: tuple[str, ...]
    public_map_ids: tuple[str, ...]
    required_source_ids: tuple[str, ...]
    street_inventory: bool
    public_map_street_scope: bool
    radius_scope_queries: bool
    public_ugc_navigation: bool
    area_entity_mode: str
    completion_policy: CompletionPolicy
    description: str


class ResearchProfileRegistry:
    """Resolve profile -> capabilities -> sources without consumer-specific code."""

    def __init__(
        self,
        *,
        capabilities: tuple[ResearchCapability, ...],
        profiles: tuple[ResearchProfile, ...],
    ) -> None:
        self._capabilities = capabilities
        self._profiles = profiles
        self._capability_by_id = self._unique_by_id(
            capabilities,
            key=lambda item: item.capability_id,
            kind="research capability",
        )
        self._profile_by_id = self._unique_by_id(
            profiles,
            key=lambda item: item.profile_id,
            kind="research profile",
        )
        self._resolved = {
            profile_id: self._resolve(profile)
            for profile_id, profile in self._profile_by_id.items()
        }

    def get(self, profile_id: str) -> ResolvedResearchProfile | None:
        return self._resolved.get(self._token(profile_id))

    def require(self, profile_id: str) -> ResolvedResearchProfile:
        profile = self.get(profile_id)
        if profile is None:
            raise ResearchProfileContractError(
                f"research profile '{profile_id}' is not registered"
            )
        return profile

    def all(self) -> tuple[ResolvedResearchProfile, ...]:
        return tuple(self._resolved[self._token(item.profile_id)] for item in self._profiles)

    def capability_catalog(self) -> tuple[ResearchCapability, ...]:
        return self._capabilities

    def _resolve(self, profile: ResearchProfile) -> ResolvedResearchProfile:
        if profile.version < 1:
            raise ResearchProfileContractError(
                f"research profile version must be >= 1: {profile.profile_id}"
            )
        selected: list[ResearchCapability] = []
        for capability_id in profile.capability_ids:
            capability = self._capability_by_id.get(self._token(capability_id))
            if capability is None:
                raise ResearchProfileContractError(
                    f"research profile '{profile.profile_id}' references unknown "
                    f"capability '{capability_id}'"
                )
            selected.append(capability)
        if not selected:
            raise ResearchProfileContractError(
                f"research profile '{profile.profile_id}' must select capabilities"
            )

        area_modes = {
            item.area_entity_mode for item in selected if item.area_entity_mode != "verified_entities"
        }
        if len(area_modes) > 1:
            raise ResearchProfileContractError(
                f"research profile '{profile.profile_id}' has conflicting area entity modes"
            )
        area_mode = next(iter(area_modes), "verified_entities")
        return ResolvedResearchProfile(
            profile_id=self._token(profile.profile_id),
            version=profile.version,
            capability_ids=tuple(self._token(item.capability_id) for item in selected),
            source_family_ids=self._ordered_union(
                item.source_family_ids for item in selected
            ),
            public_map_ids=self._ordered_union(item.public_map_ids for item in selected),
            required_source_ids=self._ordered_union(
                item.required_source_ids for item in selected
            ),
            street_inventory=any(item.street_inventory for item in selected),
            public_map_street_scope=any(item.public_map_street_scope for item in selected),
            radius_scope_queries=any(item.radius_scope_queries for item in selected),
            public_ugc_navigation=any(item.public_ugc_navigation for item in selected),
            area_entity_mode=area_mode,
            completion_policy=profile.completion_policy,
            description=profile.description,
        )

    @classmethod
    def _unique_by_id(cls, values, *, key, kind: str):
        result = {}
        for item in values:
            item_id = cls._token(key(item))
            if item_id in result:
                raise ResearchProfileContractError(f"duplicate {kind}: {item_id}")
            result[item_id] = item
        return result

    @staticmethod
    def _ordered_union(groups) -> tuple[str, ...]:
        result: list[str] = []
        for group in groups:
            for value in group:
                normalized = value.strip().casefold().replace("-", "_")
                if normalized and normalized not in result:
                    result.append(normalized)
        return tuple(result)

    @staticmethod
    def _token(value: str) -> str:
        normalized = value.strip().casefold().replace("-", "_")
        if not normalized:
            raise ResearchProfileContractError("research contract token must not be blank")
        allowed = set("abcdefghijklmnopqrstuvwxyz0123456789_.")
        if any(char not in allowed for char in normalized):
            raise ResearchProfileContractError(
                "research contract tokens must use lowercase ASCII characters"
            )
        return normalized


def _source_family(capability_id: str, family_id: str) -> ResearchCapability:
    return ResearchCapability(
        capability_id=capability_id,
        source_family_ids=(family_id,),
        required_source_ids=("generic_web", "rss_atom", "json_feed", "site_discovery"),
    )


RESEARCH_CAPABILITIES: tuple[ResearchCapability, ...] = (
    _source_family("source.official_government", "official_government"),
    _source_family("source.public_appeals", "public_appeals"),
    _source_family("source.housing_utilities", "housing_utilities"),
    _source_family("source.local_forums", "local_forums"),
    _source_family("source.local_media", "local_media"),
    _source_family("source.public_communities", "public_communities"),
    _source_family("source.general_web", "general_web"),
    ResearchCapability(
        capability_id="spatial.radius_street_inventory",
        required_source_ids=("openstreetmap_overpass",),
        street_inventory=True,
        radius_scope_queries=True,
        area_entity_mode="verified_streets_only",
        description="Source-backed named street inventory inside the analysis radius.",
    ),
    ResearchCapability(
        capability_id="public_maps.resident_content",
        public_map_ids=("yandex_maps_web", "2gis_web", "google_maps_web"),
        required_source_ids=("generic_web",),
        public_map_street_scope=True,
        radius_scope_queries=True,
        public_ugc_navigation=True,
        description="Public map cards and resident content with information-only delivery.",
    ),
)


RESEARCH_PROFILES: tuple[ResearchProfile, ...] = (
    ResearchProfile(
        profile_id="urban_signals",
        version=1,
        capability_ids=(
            "source.official_government",
            "source.public_appeals",
            "source.housing_utilities",
            "source.local_forums",
            "source.local_media",
            "source.public_communities",
            "source.general_web",
            "spatial.radius_street_inventory",
            "public_maps.resident_content",
        ),
        completion_policy=CompletionPolicy(
            mandatory_source_families=True,
            mandatory_public_maps=True,
            strict_sequential=True,
            skip_generic_discovery_after_mandatory=True,
        ),
        description="Exhaustive radius-aware urban public-signal research.",
    ),
    ResearchProfile(
        profile_id="test_public_context",
        version=1,
        capability_ids=(
            "source.official_government",
            "source.local_media",
        ),
        completion_policy=CompletionPolicy(
            mandatory_source_families=True,
            strict_sequential=True,
            skip_generic_discovery_after_mandatory=True,
            optional_pages=4,
            optional_duration_seconds=30.0,
        ),
        description=(
            "Artificial non-Kraken profile proving reusable capability composition in CI."
        ),
    ),
)


RESEARCH_PROFILE_REGISTRY = ResearchProfileRegistry(
    capabilities=RESEARCH_CAPABILITIES,
    profiles=RESEARCH_PROFILES,
)


def resolved_research_profile_from_request(request: object) -> ResolvedResearchProfile | None:
    """Resolve through the request's ToolPack without branching on consumer/module IDs."""

    from argus.toolpacks import resolved_tool_pack_from_request

    pack = resolved_tool_pack_from_request(request)
    if pack is None:
        return None
    profile = RESEARCH_PROFILE_REGISTRY.get(pack.planner_policy)
    if profile is None:
        return None
    denied = [
        source_id
        for source_id in profile.required_source_ids
        if not pack.allows_source(source_id)
    ]
    if denied:
        raise ResearchProfileContractError(
            f"tool pack '{pack.tool_pack_id}' does not allow profile sources: "
            f"{', '.join(denied)}"
        )
    return profile


def research_profile_catalog() -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for profile in RESEARCH_PROFILE_REGISTRY.all():
        completion = profile.completion_policy
        result.append(
            {
                "profile_id": profile.profile_id,
                "version": profile.version,
                "capabilities": list(profile.capability_ids),
                "source_families": list(profile.source_family_ids),
                "public_maps": list(profile.public_map_ids),
                "required_source_ids": list(profile.required_source_ids),
                "street_inventory": profile.street_inventory,
                "public_map_street_scope": profile.public_map_street_scope,
                "completion_policy": {
                    "mandatory_source_families": completion.mandatory_source_families,
                    "mandatory_public_maps": completion.mandatory_public_maps,
                    "strict_sequential": completion.strict_sequential,
                    "skip_generic_discovery_after_mandatory": (
                        completion.skip_generic_discovery_after_mandatory
                    ),
                    "emergency_max_pages": completion.emergency_max_pages,
                    "emergency_max_duration_seconds": (
                        completion.emergency_max_duration_seconds
                    ),
                    "optional_pages": completion.optional_pages,
                    "optional_duration_seconds": completion.optional_duration_seconds,
                },
                "description": profile.description,
            }
        )
    return result
