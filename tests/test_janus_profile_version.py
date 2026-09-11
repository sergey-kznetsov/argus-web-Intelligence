from argus.consumer_registry import CONSUMER_PROFILE_REGISTRY


def test_janus_consumer_profile_version_is_one() -> None:
    profile = CONSUMER_PROFILE_REGISTRY.get("janus.parking.potential.uds")
    assert profile is not None
    assert profile.version == 1
    assert profile.default_capability == "residential_facts"
