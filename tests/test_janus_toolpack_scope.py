from argus.toolpacks import TOOL_PACK_REGISTRY


def test_janus_toolpack_uses_residential_source_path_only() -> None:
    pack = TOOL_PACK_REGISTRY.get("janus.residential_facts")
    assert pack is not None
    assert pack.consumer_id == "janus.parking.potential.uds"
    assert pack.capability == "residential_facts"
    assert "mingkh_residential" in pack.allowed_source_ids
    assert "openstreetmap_overpass" not in pack.allowed_source_ids
