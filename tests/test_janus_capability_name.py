from argus.consumer_registry import CONSUMER_PROFILE_REGISTRY


def test_janus_capability_identity_is_stable() -> None:
    profile = CONSUMER_PROFILE_REGISTRY.get("janus.parking.potential.uds")
    assert profile is not None
    capability = profile.capability("residential_facts")
    assert capability is not None
    assert capability.capability == "residential_facts"
