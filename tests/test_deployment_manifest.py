import json
import tomllib
from pathlib import Path

from argus import __version__
from argus.module_protocol import MODULE_ID, runtime_manifest


ROOT = Path(__file__).resolve().parents[1]


def test_deployment_manifest_matches_hidden_runtime_contract():
    deployment = json.loads((ROOT / "geo-analyzer-module.json").read_text(encoding="utf-8"))
    runtime = runtime_manifest()
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert deployment["schema_version"] == 1
    module = deployment["module"]
    assert module["module_id"] == MODULE_ID == runtime["module_id"]
    assert module["display_name"] == runtime["display_name"]
    assert (
        module["module_version"]
        == __version__
        == runtime["module_version"]
        == pyproject["project"]["version"]
    )
    assert module["protocol_version"] == "1.0.0" == runtime["protocol_version"]

    assert deployment["runtime"]["kind"] == "python"
    assert deployment["runtime"]["install"]

    database = deployment["database"]
    assert database["required"] is True
    assert database["engine"] == "postgresql"
    assert database["dsn_env"] == "GEOANALYZER_DATABASE_DSN"
    assert database["dsn_file_env"] == "GEOANALYZER_DATABASE_DSN_FILE"
    assert ["{python}", "-m", "argus.runtime_entrypoint", "storage", "migrate"] in database[
        "migrations"
    ]
    assert ["{python}", "-m", "argus.runtime_entrypoint", "storage", "check"] in database[
        "migrations"
    ]

    assert deployment["secrets"]["auth_token_file_env"] == "ARGUS_TOKEN_FILE"
    processes = {item["id"]: item for item in deployment["processes"]}
    assert set(processes) == {"api", "worker"}

    api_process = processes["api"]
    assert api_process["command"][:4] == [
        "{python}",
        "-m",
        "argus.runtime_entrypoint",
        "api",
    ]
    assert "127.0.0.1" in api_process["command"]
    assert "{api_port}" in api_process["command"]
    assert api_process["environment"]["ARGUS_EXECUTION_ROLE"] == "api"
    assert api_process["environment"]["ARGUS_STORAGE_BACKEND"] == "postgresql"
    assert api_process["health"] == {"path": "/v1/health", "authenticated": True}

    worker_process = processes["worker"]
    assert worker_process["role"] == "worker"
    assert worker_process["command"][:4] == [
        "{python}",
        "-m",
        "argus.runtime_entrypoint",
        "worker",
    ]
    assert "{worker_probe_port}" in worker_process["command"]
    assert worker_process["environment"]["ARGUS_EXECUTION_ROLE"] == "worker"
    assert worker_process["environment"]["ARGUS_STORAGE_BACKEND"] == "postgresql"
    assert worker_process["health"] == {
        "path": "/readyz",
        "port_placeholder": "worker_probe_port",
        "authenticated": False,
    }

    assert deployment["ports"]["api"]["host"] == "127.0.0.1"
    assert deployment["ports"]["worker_probe"]["host"] == "127.0.0.1"

    integration = deployment["integration"]
    assert integration["endpoint_process"] == "api"
    assert integration["endpoint_port"] == "api_port"
    assert integration["auto_enable_after_healthcheck"] is True

    assert runtime["ui"] == {
        "optional": False,
        "default_enabled": True,
        "analysis_launch_toggle": False,
        "capability_card": False,
    }
