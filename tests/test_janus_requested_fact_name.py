from argus.consumer_registry import CONSUMER_PROFILE_REGISTRY


def test_janus_default_fact_is_residential_premises_count() -> None:
    profile = CONSUMER_PROFILE_REGISTRY.get("janus.parking.potential.uds")
    assert profile is not None
    capability = profile.capability("residential_facts")
    assert capability is not None
    assert capability.default_requested_facts == ("residential_premises_count",)
