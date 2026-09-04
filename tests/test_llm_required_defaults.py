from pathlib import Path

from argus.config import Settings


def test_ollama_is_required_by_product_default() -> None:
    fields = Settings.model_fields

    assert fields["llm_required"].default is True
    assert fields["ollama_url"].default == "http://127.0.0.1:11434"
    assert fields["ollama_model"].default == "qwen3:8b"


def test_standalone_deployment_requires_ollama() -> None:
    script = (
        Path(__file__).resolve().parents[1] / "deploy" / "windows" / "deploy-server.ps1"
    ).read_text(encoding="utf-8")

    assert 'ARGUS_LLM_REQUIRED = "true"' in script
    assert 'ARGUS_LLM_REQUIRED = "false"' not in script
