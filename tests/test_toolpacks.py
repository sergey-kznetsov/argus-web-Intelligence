from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from argus.contracts.models import CollectionRequest
from argus.sources.base import SourceResult
from argus.sources.registry import SourceRegistry
from argus.toolpacks import (
    ToolPackSourceDeniedError,
    activate_tool_pack,
    resolved_tool_pack_from_request,
    source_allowed_by_active_tool_pack,
    tool_pack_catalog,
)


class _Adapter:
    def __init__(self, source_id: str, intents: set[str]) -> None:
        self.source_id = source_id
        self.intents = intents

    async def discover(self, request):
        return []

    async def fetch(self, task):
        return object()

    async def extract(self, task, fetched, request):
        return SourceResult(observations=[])

    async def normalize(self, result):
        return result

    async def health(self):
        return {"status": "ok"}


def _request(consumer: str = "kraken.development.uds", **overrides) -> CollectionRequest:
    payload = {
        "consumer": consumer,
        "analysis_id": "tool-pack-test",
        "territory": {
            "city": "Ижевск",
            "address": "Ижевск, Пушкинская, 277",
        },
        "intents": ["reviews"],
    }
    payload.update(overrides)
    return CollectionRequest(**payload)


def test_kraken_contract_resolves_versioned_tool_pack():
    request = _request()

    assert request.tool_pack_id == "kraken.urban_signals"
    assert request.tool_pack_version == 1
    pack = resolved_tool_pack_from_request(request)
    assert pack is not None
    assert pack.consumer_id == "kraken.development.uds"
    assert pack.capability == "urban_signals"
    assert pack.planner_policy == "urban_signals"
    assert pack.recipe_namespace == "kraken.urban_signals"


def test_caller_cannot_override_profile_tool_pack():
    with pytest.raises(ValidationError, match="UNSUPPORTED_TOOL_PACK"):
        _request(tool_pack_id="test.generic")

    with pytest.raises(ValidationError, match="UNSUPPORTED_TOOL_PACK_VERSION"):
        _request(tool_pack_version=2)


def test_legacy_consumer_cannot_select_tool_pack():
    request = _request(consumer="legacy.module")
    assert request.tool_pack_id is None
    assert request.tool_pack_version is None
    assert resolved_tool_pack_from_request(request) is None

    with pytest.raises(ValidationError, match="UNKNOWN_CONSUMER"):
        _request(consumer="legacy.module", capability="generic_research")


def test_kraken_tool_pack_blocks_residential_and_historical_only_sources():
    registry = SourceRegistry()
    registry.register(_Adapter("generic_web", {"reviews", "residential_population"}))
    registry.register(_Adapter("mingkh_residential", {"residential_population"}))
    registry.register(_Adapter("pastvu_historical", {"historical_context"}))

    request = _request(intents=["reviews", "residential_population", "historical_context"])
    pack = resolved_tool_pack_from_request(request)
    assert pack is not None

    with activate_tool_pack(pack):
        selected = {adapter.source_id for adapter in registry.for_intents(request.intents)}
        assert selected == {"generic_web"}
        assert registry.get("generic_web").source_id == "generic_web"
        with pytest.raises(ToolPackSourceDeniedError):
            registry.get("mingkh_residential")
        with pytest.raises(ToolPackSourceDeniedError):
            registry.get("pastvu_historical")


def test_source_registry_can_resolve_selection_directly_from_request():
    registry = SourceRegistry()
    registry.register(_Adapter("generic_web", {"reviews"}))
    registry.register(_Adapter("mingkh_residential", {"reviews"}))

    selected = {adapter.source_id for adapter in registry.for_request(_request())}

    assert selected == {"generic_web"}


def test_tool_pack_context_is_isolated_between_concurrent_collections():
    kraken_pack = resolved_tool_pack_from_request(_request())
    test_pack = resolved_tool_pack_from_request(_request(consumer="test"))
    assert kraken_pack is not None
    assert test_pack is not None

    async def check(pack, expected: bool) -> None:
        with activate_tool_pack(pack):
            await asyncio.sleep(0)
            assert source_allowed_by_active_tool_pack("mingkh_residential") is expected
            await asyncio.sleep(0)
            assert source_allowed_by_active_tool_pack("mingkh_residential") is expected

    async def run() -> None:
        await asyncio.gather(
            check(kraken_pack, False),
            check(test_pack, True),
        )

    asyncio.run(run())


def test_tool_pack_catalog_is_explicit_and_consumer_scoped():
    catalog = {item["tool_pack_id"]: item for item in tool_pack_catalog()}
    kraken = catalog["kraken.urban_signals"]

    assert kraken["consumer_id"] == "kraken.development.uds"
    assert kraken["capability"] == "urban_signals"
    assert "generic_web" in kraken["allowed_source_ids"]
    assert "mingkh_residential" not in kraken["allowed_source_ids"]
    assert "pastvu_historical" not in kraken["allowed_source_ids"]
