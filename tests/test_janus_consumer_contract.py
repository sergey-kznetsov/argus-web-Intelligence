from __future__ import annotations

import pytest

from argus.consumer_registry import CONSUMER_PROFILE_REGISTRY, ConsumerContractError
from argus.contracts.models import CollectionRequest, TerritoryContext
from argus.toolpacks import resolved_tool_pack_from_request


def _request(**updates):
    payload = {
        "consumer": "janus.parking.potential.uds",
        "consumer_profile_version": 1,
        "capability": "residential_facts",
        "requested_facts": ["residential_premises_count"],
        "analysis_id": "janus-test",
        "territory": TerritoryContext(
            city="Ижевск",
            address="Ижевск, Пушкинская улица, 115",
        ),
        "intents": ["residential_premises_count"],
    }
    payload.update(updates)
    return CollectionRequest(**payload)


def test_janus_profile_allows_only_residential_premises_fact():
    profile = CONSUMER_PROFILE_REGISTRY.get("janus.parking.potential.uds")
    assert profile is not None
    capability = profile.capability("residential_facts")
    assert capability is not None
    assert capability.allowed_facts == ("residential_premises_count",)


def test_janus_request_resolves_to_mingkh_bounded_tool_pack():
    request = _request()
    pack = resolved_tool_pack_from_request(request)
    assert pack is not None
    assert pack.tool_pack_id == "janus.residential_facts"
    assert pack.allows_source("mingkh_residential")
    assert pack.allows_source("site_discovery")
    assert not pack.allows_source("openstreetmap_overpass")


def test_janus_cannot_expand_contract_to_population_or_parking_sources():
    with pytest.raises((ConsumerContractError, ValueError)):
        _request(requested_facts=["residential_population"])
    with pytest.raises((ConsumerContractError, ValueError)):
        _request(requested_facts=["parking_capacity"])
