import pytest

from argus.consumer_registry import CONSUMER_PROFILE_REGISTRY, ConsumerContractError


def test_janus_profile_allows_only_residential_premises_count() -> None:
    resolved = CONSUMER_PROFILE_REGISTRY.resolve(
        consumer="janus.parking.potential.uds",
        capability="residential_facts",
        requested_facts=["residential_premises_count"],
        profile_version=1,
    )
    assert resolved.requested_facts == ("residential_premises_count",)

    with pytest.raises(ConsumerContractError):
        CONSUMER_PROFILE_REGISTRY.resolve(
            consumer="janus.parking.potential.uds",
            capability="residential_facts",
            requested_facts=["parking_capacity"],
            profile_version=1,
        )
