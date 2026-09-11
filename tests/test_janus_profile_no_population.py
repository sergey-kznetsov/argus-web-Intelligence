from argus.consumer_registry import CONSUMER_PROFILE_REGISTRY


def test_janus_profile_does_not_request_population() -> None:
    profile = CONSUMER_PROFILE_REGISTRY.get("janus.parking.potential.uds")
    assert profile is not None
    capability = profile.capability("residential_facts")
    assert capability is not None
    assert "residential_population" not in capability.allowed_facts
