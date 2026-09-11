from __future__ import annotations

from argus.bootstrap import build_services
from argus.config import Settings
from argus.llm_health import OllamaRuntimeHealth
from argus.llm_runtime import LlmConcurrencyGate


def _settings(tmp_path, **updates) -> Settings:
    values = {
        "db_path": tmp_path / "argus.sqlite3",
        "token_file": tmp_path / "token",
        "browser_serp_enabled": False,
        "agent_enabled": False,
    }
    values.update(updates)
    return Settings(**values)


def test_bootstrap_wires_optional_local_llm_and_shared_gate(tmp_path):
    services = build_services(_settings(tmp_path, llm_enabled=True, llm_required=False))

    assert isinstance(services.llm_health, OllamaRuntimeHealth)
    assert isinstance(services.llm_gate, LlmConcurrencyGate)
    assert services.llm_gate.max_concurrency == 1
    assert services.llm_required_on_start is False


def test_llm_can_be_disabled_with_deterministic_fail_open_graph(tmp_path):
    services = build_services(
        _settings(tmp_path, llm_enabled=False, llm_required=False, agent_enabled=True)
    )

    assert services.llm_health is None
    assert services.llm_gate is not None
    assert services.llm_required_on_start is False


def test_required_llm_is_enforced_only_on_collecting_processes(tmp_path):
    embedded = build_services(_settings(tmp_path, llm_enabled=True, llm_required=True))
    assert embedded.llm_required_on_start is True

    api = build_services(
        _settings(
            tmp_path,
            llm_enabled=True,
            llm_required=True,
            execution_role="api",
            storage_backend="postgresql",
            database_dsn="postgresql://argus:secret@127.0.0.1:5432/argus",
        )
    )
    assert api.llm_required_on_start is False