from pathlib import Path

from argus.config import Settings


def test_resource_safe_llm_and_worker_defaults() -> None:
    settings = Settings()

    assert settings.llm_enabled is True
    assert settings.llm_required is False
    assert settings.ollama_model == "argus-qwen3:8b-cpu"
    assert settings.ollama_num_thread == 2
    assert settings.ollama_num_ctx == 4096
    assert settings.ollama_num_predict == 512
    assert settings.ollama_keep_alive_seconds == 60
    assert settings.llm_max_concurrency == 1
    assert settings.worker_concurrency == 1
    assert settings.max_concurrency == 2
    assert settings.browser_max_concurrency == 1
    assert settings.agent_enabled is True
    assert settings.agent_backend == "auto"


def test_standalone_deployment_uses_optional_fail_open_llm_profile() -> None:
    script = (
        Path(__file__).resolve().parents[1] / "deploy" / "windows" / "deploy-server.ps1"
    ).read_text(encoding="utf-8")

    assert 'ARGUS_LLM_ENABLED = "true"' in script
    assert 'ARGUS_LLM_REQUIRED = "false"' in script
    assert 'ARGUS_LLM_MAX_CONCURRENCY = "1"' in script
    assert 'ARGUS_AGENT_BACKEND = "auto"' in script
    assert ".[stagehand]" in script
    assert ".[agent-browser-use]" in script
    assert '"-m", "pip", "check"' in script