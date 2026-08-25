from __future__ import annotations

import json
from pathlib import Path

from argus import __version__
from argus.module_protocol import MODULE_ID, runtime_manifest


ROOT = Path(__file__).resolve().parents[1]
DEPLOYMENT_MANIFEST = ROOT / "geo-analyzer-module.json"


def _deployment() -> dict[str, object]:
    return json.loads(DEPLOYMENT_MANIFEST.read_text(encoding="utf-8"))


def test_deployment_and_runtime_manifest_identity_stay_in_sync() -> None:
    deployment = _deployment()
    declared = deployment["module"]
    runtime = runtime_manifest()

    assert deployment["schema_version"] == 1
    assert declared["module_id"] == MODULE_ID == runtime["module_id"]
    assert declared["module_version"] == __version__ == runtime["module_version"]
    assert declared["protocol_version"] == runtime["protocol_version"]
    assert declared["display_name"] == runtime["display_name"]


def test_argus_is_enabled_infrastructure_but_never_an_analysis_launch_module() -> None:
    deployment = _deployment()
    runtime = runtime_manifest()
    ui = runtime["ui"]

    assert deployment["integration"]["auto_enable_after_healthcheck"] is True
    assert ui == {
        "optional": False,
        "default_enabled": True,
        "analysis_launch_toggle": False,
        "capability_card": False,
    }
    assert "infrastructure_service" in runtime["capabilities"]


def test_deployment_uses_shared_postgres_and_bounded_local_processes() -> None:
    deployment = _deployment()
    database = deployment["database"]
    processes = deployment["processes"]
    ports = deployment["ports"]

    assert database["required"] is True
    assert database["engine"] == "postgresql"
    assert database["dsn_env"] == "GEOANALYZER_DATABASE_DSN"
    assert database["dsn_file_env"] == "GEOANALYZER_DATABASE_DSN_FILE"
    assert database["migrations"] == [
        ["{python}", "-m", "argus.storage.cli", "migrate"],
        ["{python}", "-m", "argus.storage.cli", "check"],
    ]

    assert {process["id"] for process in processes} == {"api", "worker"}
    api = next(process for process in processes if process["id"] == "api")
    worker = next(process for process in processes if process["id"] == "worker")
    assert api["command"][-4:] == ["--host", "127.0.0.1", "--port", "{api_port}"]
    assert worker["command"][-4:] == [
        "--probe-host",
        "127.0.0.1",
        "--probe-port",
        "{worker_probe_port}",
    ]
    assert api["environment"]["ARGUS_EXECUTION_ROLE"] == "api"
    assert worker["environment"]["ARGUS_EXECUTION_ROLE"] == "worker"
    assert api["environment"]["ARGUS_STORAGE_BACKEND"] == "postgresql"
    assert worker["environment"]["ARGUS_STORAGE_BACKEND"] == "postgresql"
    assert ports["api"]["host"] == "127.0.0.1"
    assert ports["worker_probe"]["host"] == "127.0.0.1"


def test_deployment_install_and_health_contract_match_geo_analyzer_manager() -> None:
    deployment = _deployment()
    install = deployment["runtime"]["install"]
    integration = deployment["integration"]
    processes = deployment["processes"]

    assert deployment["runtime"]["kind"] == "python"
    assert install[0] == [
        "{python}",
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        ".",
    ]
    assert install[1] == ["{python}", "-m", "playwright", "install", "chromium"]
    assert deployment["secrets"]["auth_token_file_env"] == "ARGUS_TOKEN_FILE"
    assert integration["endpoint_process"] == "api"
    assert integration["endpoint_port"] == "api_port"
    assert integration["endpoint_scheme"] == "http"

    api = next(process for process in processes if process["id"] == "api")
    worker = next(process for process in processes if process["id"] == "worker")
    assert api["health"] == {"path": "/v1/health", "authenticated": True}
    assert worker["health"] == {
        "path": "/readyz",
        "port": "worker_probe_port",
        "authenticated": False,
    }
