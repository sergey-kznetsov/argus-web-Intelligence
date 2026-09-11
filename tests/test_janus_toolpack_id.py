from argus.toolpacks import TOOL_PACK_REGISTRY


def test_janus_toolpack_identity_is_stable() -> None:
    pack = TOOL_PACK_REGISTRY.get("janus.residential_facts")
    assert pack is not None
    assert pack.tool_pack_id == "janus.residential_facts"
