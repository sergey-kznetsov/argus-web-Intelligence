from argus.consumer_registry import CONSUMER_PROFILE_REGISTRY


def test_janus_consumer_description_is_residential_only() -> None:
    profile = CONSUMER_PROFILE_REGISTRY.get("janus.parking.potential.uds")
    assert profile is not None
    description = profile.description.casefold()
    assert "residential" in description or "жил" in description
