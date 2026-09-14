from argus.toolpacks import TOOL_PACK_REGISTRY


def test_janus_toolpack_is_single_source_single_domain() -> None:
    pack = TOOL_PACK_REGISTRY.get("janus.residential_facts")
    assert pack is not None
    assert pack.consumer_id == "janus.parking.potential.uds"
    assert pack.capability == "residential_facts"
    assert pack.allowed_source_ids == ("mingkh_residential",)
    assert pack.exclusive_domains == ("dom.mingkh.ru",)
    assert pack.planner_policy == "janus_residential_facts"
    assert pack.max_pages == 1
    assert pack.max_depth == 0
    assert "site_discovery" not in pack.allowed_source_ids
    assert "generic_web" not in pack.allowed_source_ids
    assert "openstreetmap_overpass" not in pack.allowed_source_ids
