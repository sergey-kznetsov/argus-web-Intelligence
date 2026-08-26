from __future__ import annotations

from argus.bootstrap import build_services
from argus.config import Settings
from argus.llm_health import OllamaRuntimeHealth


def _settings(tmp_path, **updates) -> Settings:
    values = {
        "db_path": tmp_path / "argus.sqlite3",
        "token_file": tmp_path / "token",
        "browser_serp_enabled": False,
        "agent_enabled": False,
    }
    values.update(updates)
    return Settings(**values)


def test_bootstrap_wires_local_llm_health_without_making_it_required_by_default(tmp_path):
    services = build_services(_settings(tmp_path))

    assert isinstance(services.llm_health, OllamaRuntimeHealth)
    assert services.llm_required_on_start is False


def test_embedded_ai_profile_requires_local_llm_when_configured(tmp_path):
    services = build_services(
        _settings(
            tmp_path,
            execution_role="embedded",
            llm_required=True,
        )
    )

    assert isinstance(services.llm_health, OllamaRuntimeHealth)
    assert services.llm_required_on_start is True
