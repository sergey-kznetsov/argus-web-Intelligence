from argus.toolpacks import TOOL_PACK_REGISTRY


def test_janus_toolpack_has_no_source_wildcard() -> None:
    pack = TOOL_PACK_REGISTRY.get("janus.residential_facts")
    assert pack is not None
    assert "*" not in pack.allowed_source_ids
