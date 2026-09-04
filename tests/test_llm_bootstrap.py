from __future__ import annotations

from argus.bootstrap import build_services
from argus.config import Settings


def _settings(tmp_path, **updates) -> Settings:
    values = {
        "db_path": tmp_path / "argus.sqlite3",
        "token_file": tmp_path / "token",
        "browser_serp_enabled": False,
        "agent_enabled": False,
    }
    values.update(updates)
    return Settings(**values)


def test_bootstrap_has_no_local_llm_health_dependency(tmp_path):
    services = build_services(_settings(tmp_path, llm_required=False))

    assert services.llm_health is None
    assert services.llm_required_on_start is False


def test_legacy_llm_required_flag_cannot_restore_runtime_dependency(tmp_path):
    services = build_services(
        _settings(
            tmp_path,
            execution_role="embedded",
            llm_required=True,
        )
    )

    assert services.llm_health is None
    assert services.llm_required_on_start is False
