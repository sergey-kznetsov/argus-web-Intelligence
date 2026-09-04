from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy" / "windows" / "deploy-server.ps1"
RUNNER = ROOT / "deploy" / "windows" / "run-process.ps1"
CONSUMER = ROOT / "deploy" / "windows" / "configure-geo-analyzer-consumer.ps1"


def test_argus_is_not_installable_as_geo_analyzer_module() -> None:
    assert not (ROOT / "geo-analyzer-module.json").exists()


def test_windows_server_deployment_is_standalone_and_loopback_only() -> None:
    deploy = DEPLOY.read_text(encoding="utf-8")
    runner = RUNNER.read_text(encoding="utf-8")

    assert 'C:\\argus' in deploy
    assert 'C:\\ProgramData\\ARGUS' in deploy
    assert '"ARGUS-API"' in deploy
    assert '"ARGUS-Worker"' in deploy
    assert "8787" in deploy
    assert "8788" in deploy
    assert "127.0.0.1" in deploy
    assert "ARGUS standalone deployment succeeded." in deploy
    assert "ARGUS_STORAGE_BACKEND = \"postgresql\"" in deploy
    assert "ARGUS_DATABASE_DSN_FILE" in deploy
    assert "ARGUS_TOKEN_FILE" in deploy
    assert "ARGUS_EXECUTION_ROLE = \"api\"" in runner
    assert "ARGUS_EXECUTION_ROLE = \"worker\"" in runner
    assert '"--host", "127.0.0.1"' in runner
    assert '"--probe-host", "127.0.0.1"' in runner
    assert '$ErrorActionPreference = "Continue"' in runner
    assert "exit $processExitCode" in runner


def test_deployment_owns_its_database_and_github_configuration() -> None:
    deploy = DEPLOY.read_text(encoding="utf-8")

    assert 'database-dsn.txt' in deploy
    assert 'github-token.txt' in deploy
    assert "ARGUS_GITHUB_TOKEN" in deploy
    assert "standalone ARGUS must use PostgreSQL database 'argus'" in deploy
    assert "standalone ARGUS must use PostgreSQL service role 'argus'" in deploy
    assert "database_dsn_file" in deploy

    forbidden = (
        "DatabaseSourceEnvFile",
        "GEOANALYZER_DATABASE_DSN",
        "GEOANALYZER_DATABASE_DSN_FILE",
        "C:\\server\\saas.env",
        "C:\\ProgramData\\GeoAnalyzerTest\\saas.env",
    )
    for value in forbidden:
        assert value not in deploy


def test_windows_runtime_replaces_only_verified_stale_argus_port_owner() -> None:
    runner = RUNNER.read_text(encoding="utf-8")

    assert "function Ensure-ArgusRuntimePort" in runner
    assert "Get-NetTCPConnection" in runner
    assert "Get-CimInstance Win32_Process" in runner
    assert "argus.runtime_entrypoint" in runner
    assert "taskkill.exe" in runner
    assert "previous-release" in runner
    assert "same-release" in runner
    assert "refusing to terminate it" in runner


def test_deployment_health_must_be_served_by_the_new_release() -> None:
    deploy = DEPLOY.read_text(encoding="utf-8")

    assert "function Test-ProcessBelongsToRelease" in deploy
    assert "function Assert-ArgusListenersOwnedByRelease" in deploy
    assert "Get-NetTCPConnection" in deploy
    assert "Get-CimInstance Win32_Process" in deploy
    assert "run-process.ps1" in deploy
    assert "ExpectedRelease" in deploy
    assert "is served by a different release" in deploy
    assert "-ExpectedRelease $releaseDir" in deploy
    assert "-ExpectedRelease $previousRelease" in deploy


def test_geo_analyzer_consumers_receive_generic_argus_service_contract() -> None:
    consumer = CONSUMER.read_text(encoding="utf-8")

    assert "ARGUS_SERVICE_BASE_URL" in consumer
    assert "ARGUS_SERVICE_TOKEN_FILE" in consumer
    assert "http://127.0.0.1:8787" in consumer
    assert "C:\\ProgramData\\ARGUS\\secrets\\argus.token" in consumer
    assert "KRAKEN_ARGUS_BASE_URL" not in consumer
    assert "KRAKEN_ARGUS_TOKEN_FILE" not in consumer
    assert "Geo Analyzer consumer configuration applied." in consumer
