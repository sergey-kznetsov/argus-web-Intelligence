from argus.consumer_registry import CONSUMER_PROFILE_REGISTRY


def test_janus_consumer_identity_is_stable() -> None:
    profile = CONSUMER_PROFILE_REGISTRY.get("janus.parking.potential.uds")
    assert profile is not None
    assert profile.consumer_id == "janus.parking.potential.uds"
