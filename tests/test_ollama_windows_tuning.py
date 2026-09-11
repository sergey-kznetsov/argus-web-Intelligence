from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_windows_ollama_profile_enforces_single_bounded_cpu_runtime() -> None:
    script = (ROOT / "deploy/windows/tune-ollama-cpu.ps1").read_text(encoding="utf-8")

    for contract in (
        'OLLAMA_NUM_PARALLEL = "1"',
        'OLLAMA_MAX_LOADED_MODELS = "1"',
        'OLLAMA_MAX_QUEUE = "4"',
        'OLLAMA_KEEP_ALIVE = "60s"',
        '"PARAMETER num_ctx 4096"',
        '"PARAMETER num_predict 512"',
        "BelowNormal",
        "ProcessorAffinity",
    ):
        assert contract in script


def test_windows_deploy_keeps_stagehand_and_browser_use_dependencies_isolated() -> None:
    script = (ROOT / "deploy/windows/deploy-server.ps1").read_text(encoding="utf-8")

    assert '".[stagehand]"' in script
    assert '".[agent-browser-use]"' in script
    assert ".venv-browser-use" in script
    assert "ARGUS_BROWSER_USE_PYTHON = $browserUsePython" in script
    assert script.count('Arguments @("-m", "pip", "check")') >= 2
