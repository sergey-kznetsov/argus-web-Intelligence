from pathlib import Path


RUNNER = Path("deploy/windows/run-process.ps1")


def test_windows_runner_traces_release_ownership_through_process_chain() -> None:
    script = RUNNER.read_text(encoding="utf-8")

    assert "function Test-ProcessChainContainsPath" in script
    assert "$process.ParentProcessId" in script
    assert "argus.runtime_entrypoint" in script
    assert "CPython venvs on Windows can expose the base interpreter" in script
    assert "-PathPrefix $ReleasesRoot" in script
    assert "-PathPrefix $CurrentRelease" in script


def test_windows_runner_keeps_unmanaged_port_cleanup_fail_closed() -> None:
    script = RUNNER.read_text(encoding="utf-8")

    assert "refusing to terminate it" in script
    assert "refusing unsafe cleanup" in script
    assert "taskkill.exe" in script
    assert "/T /F" in script
