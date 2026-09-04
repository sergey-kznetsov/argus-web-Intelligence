from pathlib import Path

from argus.config import Settings


def test_llm_is_disabled_by_product_default() -> None:
    fields = Settings.model_fields

    assert fields["llm_required"].default is False
    assert fields["agent_enabled"].default is False
    assert fields["agent_backend"].default == "disabled"


def test_standalone_deployment_keeps_llm_disabled() -> None:
    script = (
        Path(__file__).resolve().parents[1] / "deploy" / "windows" / "deploy-server.ps1"
    ).read_text(encoding="utf-8")

    assert 'ARGUS_LLM_REQUIRED = "false"' in script
    assert 'ARGUS_LLM_REQUIRED = "true"' not in script
