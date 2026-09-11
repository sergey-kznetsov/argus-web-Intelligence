from argus.toolpacks import TOOL_PACK_REGISTRY


def test_janus_toolpack_version_is_one() -> None:
    pack = TOOL_PACK_REGISTRY.get("janus.residential_facts")
    assert pack is not None
    assert pack.version == 1
