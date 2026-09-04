from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator


class ToolPackContractError(ValueError):
    """Raised when a consumer tries to select an invalid ARGUS tool pack."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class ToolPackSourceDeniedError(RuntimeError):
    """Raised before a source adapter outside the active tool pack can execute."""

    def __init__(self, *, source_id: str, tool_pack_id: str) -> None:
        self.source_id = source_id
        self.tool_pack_id = tool_pack_id
        super().__init__(
            f"source '{source_id}' is not enabled by active tool pack '{tool_pack_id}'"
        )


@dataclass(frozen=True, slots=True)
class ToolPack:
    """Versioned execution policy for one consumer capability.

    Tool packs select ARGUS tooling; they do not contain downstream analytical logic.
    Shared crawler runtimes, Evidence/Provenance, storage and security remain in ARGUS Core.
    """

    tool_pack_id: str
    version: int
    consumer_id: str
    capability: str
    allowed_source_ids: tuple[str, ...]
    shared_tools: tuple[str, ...] = (
        "fast",
        "browser",
        "agent",
        "evidence",
        "provenance",
        "snapshots",
    )
    planner_policy: str = "universal"
    recipe_namespace: str = "shared"
    extractor_policy: str = "universal"
    result_delivery_policy: str = "intent_evidence"
    result_dedup_policy: str = "none"
    description: str = ""

    def allows_source(self, source_id: str) -> bool:
        return "*" in self.allowed_source_ids or source_id in self.allowed_source_ids


@dataclass(frozen=True, slots=True)
class ResolvedToolPack:
    tool_pack_id: str
    version: int
    consumer_id: str
    capability: str
    allowed_source_ids: tuple[str, ...]
    shared_tools: tuple[str, ...]
    planner_policy: str
    recipe_namespace: str
    extractor_policy: str
    result_delivery_policy: str
    result_dedup_policy: str

    def allows_source(self, source_id: str) -> bool:
        return "*" in self.allowed_source_ids or source_id in self.allowed_source_ids


class ToolPackRegistry:
    """Data-driven registry keeping consumer tooling isolated from ARGUS Core."""

    def __init__(self, packs: tuple[ToolPack, ...]) -> None:
        self._packs = packs
        by_id: dict[str, ToolPack] = {}
        by_contract: dict[tuple[str, str], ToolPack] = {}
        for pack in packs:
            pack_id = self._token(pack.tool_pack_id, "tool_pack_id")
            consumer_id = pack.consumer_id.strip().casefold()
            capability = self._token(pack.capability, "capability")
            if pack.version < 1:
                raise ValueError(f"tool pack version must be >= 1: {pack.tool_pack_id}")
            if pack_id in by_id:
                raise ValueError(f"duplicate tool_pack_id: {pack.tool_pack_id}")
            contract_key = (consumer_id, capability)
            if contract_key in by_contract:
                raise ValueError(
                    "duplicate consumer/capability tool pack: "
                    f"{pack.consumer_id}/{pack.capability}"
                )
            if not pack.allowed_source_ids:
                raise ValueError(f"tool pack must allow at least one source: {pack.tool_pack_id}")
            by_id[pack_id] = pack
            by_contract[contract_key] = pack
        self._by_id = by_id
        self._by_contract = by_contract

    def all(self) -> tuple[ToolPack, ...]:
        return self._packs

    def get(self, tool_pack_id: str) -> ToolPack | None:
        return self._by_id.get(self._token(tool_pack_id, "tool_pack_id"))

    def resolve(
        self,
        *,
        consumer_id: str,
        capability: str,
        expected_tool_pack_id: str,
        requested_tool_pack_id: str | None,
        requested_version: int | None,
    ) -> ResolvedToolPack:
        expected = self._token(expected_tool_pack_id, "tool_pack_id")
        pack = self._by_id.get(expected)
        if pack is None:
            raise ToolPackContractError(
                "TOOL_PACK_NOT_REGISTERED",
                f"tool pack '{expected}' is not registered",
            )

        consumer_key = consumer_id.strip().casefold()
        capability_key = self._token(capability, "capability")
        if (
            pack.consumer_id.strip().casefold() != consumer_key
            or self._token(pack.capability, "capability") != capability_key
        ):
            raise ToolPackContractError(
                "TOOL_PACK_CONTRACT_MISMATCH",
                (
                    f"tool pack '{pack.tool_pack_id}' does not belong to "
                    f"'{consumer_id}/{capability}'"
                ),
            )

        if requested_tool_pack_id is not None:
            requested = self._token(requested_tool_pack_id, "tool_pack_id")
            if requested != expected:
                raise ToolPackContractError(
                    "UNSUPPORTED_TOOL_PACK",
                    (
                        f"consumer '{consumer_id}' capability '{capability}' requires "
                        f"tool pack '{expected}', got '{requested}'"
                    ),
                )
        if requested_version is not None and requested_version != pack.version:
            raise ToolPackContractError(
                "UNSUPPORTED_TOOL_PACK_VERSION",
                (
                    f"tool pack '{pack.tool_pack_id}' supports version {pack.version}, "
                    f"got {requested_version}"
                ),
            )

        return ResolvedToolPack(
            tool_pack_id=pack.tool_pack_id,
            version=pack.version,
            consumer_id=pack.consumer_id,
            capability=pack.capability,
            allowed_source_ids=pack.allowed_source_ids,
            shared_tools=pack.shared_tools,
            planner_policy=pack.planner_policy,
            recipe_namespace=pack.recipe_namespace,
            extractor_policy=pack.extractor_policy,
            result_delivery_policy=pack.result_delivery_policy,
            result_dedup_policy=pack.result_dedup_policy,
        )

    def by_contract(self, *, consumer_id: str, capability: str) -> ToolPack | None:
        return self._by_contract.get(
            (consumer_id.strip().casefold(), self._token(capability, "capability"))
        )

    @staticmethod
    def _token(value: str, field: str) -> str:
        normalized = value.strip().casefold().replace("-", "_")
        if not normalized:
            raise ToolPackContractError(
                "INVALID_TOOL_PACK_CONTRACT",
                f"{field} must not be blank",
            )
        allowed = set("abcdefghijklmnopqrstuvwxyz0123456789_.")
        if any(char not in allowed for char in normalized):
            raise ToolPackContractError(
                "INVALID_TOOL_PACK_CONTRACT",
                f"{field} must use lowercase ASCII contract tokens",
            )
        return normalized


KRAKEN_URBAN_SIGNALS_TOOL_PACK = ToolPack(
    tool_pack_id="kraken.urban_signals",
    version=1,
    consumer_id="kraken.development.uds",
    capability="urban_signals",
    allowed_source_ids=(
        "generic_web",
        "rss_atom",
        "json_feed",
        "site_discovery",
        "openstreetmap_overpass",
    ),
    planner_policy="urban_signals",
    recipe_namespace="kraken.urban_signals",
    extractor_policy="urban_signals",
    result_delivery_policy="broad_evidence_stream",
    result_dedup_policy="canonical_text_v1",
    description=(
        "Broad public-web research for Kraken. ARGUS discovers, acquires, normalizes, "
        "deduplicates exact source-backed content and preserves Evidence/Provenance; "
        "Kraken performs downstream domain relevance and social-problem filtering. "
        "Residential registry and historical-only adapters are intentionally excluded."
    ),
)

TEST_GENERIC_TOOL_PACK = ToolPack(
    tool_pack_id="test.generic",
    version=1,
    consumer_id="test",
    capability="generic_research",
    allowed_source_ids=("*",),
    planner_policy="generic_research",
    recipe_namespace="test.generic",
    extractor_policy="generic_research",
    description="Internal CI/manual smoke tool pack; not a product consumer pack.",
)

TOOL_PACK_REGISTRY = ToolPackRegistry(
    (
        KRAKEN_URBAN_SIGNALS_TOOL_PACK,
        TEST_GENERIC_TOOL_PACK,
    )
)

_ACTIVE_TOOL_PACK: ContextVar[ResolvedToolPack | None] = ContextVar(
    "argus_active_tool_pack",
    default=None,
)


@contextmanager
def activate_tool_pack(pack: ResolvedToolPack | None) -> Iterator[None]:
    """Activate one collection's tool pack without leaking across async tasks."""

    token = _ACTIVE_TOOL_PACK.set(pack)
    try:
        yield
    finally:
        _ACTIVE_TOOL_PACK.reset(token)


def active_tool_pack() -> ResolvedToolPack | None:
    return _ACTIVE_TOOL_PACK.get()


def source_allowed_by_active_tool_pack(source_id: str) -> bool:
    pack = active_tool_pack()
    return pack is None or pack.allows_source(source_id)


def resolved_tool_pack_from_request(request: object) -> ResolvedToolPack | None:
    tool_pack_id = getattr(request, "tool_pack_id", None)
    tool_pack_version = getattr(request, "tool_pack_version", None)
    consumer = getattr(request, "consumer", None)
    capability = getattr(request, "capability", None)
    if not tool_pack_id or tool_pack_version is None or not consumer or not capability:
        return None
    pack = TOOL_PACK_REGISTRY.get(str(tool_pack_id))
    if pack is None:
        raise ToolPackContractError(
            "TOOL_PACK_NOT_REGISTERED",
            f"tool pack '{tool_pack_id}' is not registered",
        )
    return TOOL_PACK_REGISTRY.resolve(
        consumer_id=str(consumer),
        capability=str(capability),
        expected_tool_pack_id=pack.tool_pack_id,
        requested_tool_pack_id=str(tool_pack_id),
        requested_version=int(tool_pack_version),
    )


def tool_pack_catalog() -> list[dict[str, object]]:
    return [
        {
            "tool_pack_id": pack.tool_pack_id,
            "version": pack.version,
            "consumer_id": pack.consumer_id,
            "capability": pack.capability,
            "allowed_source_ids": list(pack.allowed_source_ids),
            "shared_tools": list(pack.shared_tools),
            "planner_policy": pack.planner_policy,
            "recipe_namespace": pack.recipe_namespace,
            "extractor_policy": pack.extractor_policy,
            "result_delivery_policy": pack.result_delivery_policy,
            "result_dedup_policy": pack.result_dedup_policy,
            "description": pack.description,
        }
        for pack in TOOL_PACK_REGISTRY.all()
    ]
