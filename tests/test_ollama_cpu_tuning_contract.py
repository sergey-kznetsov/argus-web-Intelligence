from __future__ import annotations

from pathlib import Path


def _script() -> str:
    return Path("deploy/windows/tune-ollama-cpu.ps1").read_text(encoding="utf-8")


def test_cpu_profile_serializes_ollama_and_bounds_queue() -> None:
    script = _script()

    assert 'OLLAMA_NUM_PARALLEL = "1"' in script
    assert 'OLLAMA_MAX_LOADED_MODELS = "1"' in script
    assert "OLLAMA_MAX_QUEUE" in script
    assert "OLLAMA_KEEP_ALIVE" in script
    assert "OLLAMA_CONTEXT_LENGTH" in script


def test_cpu_profile_bounds_argus_concurrency_and_removes_hard_llm_dependency() -> None:
    script = _script()

    assert 'ARGUS_LLM_REQUIRED = "false"' in script
    assert 'ARGUS_WORKER_CONCURRENCY = "1"' in script
    assert 'ARGUS_MAX_CONCURRENCY = "2"' in script
    assert 'ARGUS_BROWSER_MAX_CONCURRENCY = "1"' in script


def test_cpu_profile_builds_resource_bounded_derived_model() -> None:
    script = _script()

    # num_thread is not a documented Modelfile PARAMETER. Windows CPU affinity is the
    # resource guard; the derived model only uses supported generation parameters.
    assert "PARAMETER num_thread" not in script
    assert "PARAMETER num_ctx $NumCtx" in script
    assert "PARAMETER num_predict $NumPredict" in script
    assert "PARAMETER temperature 0" in script
    assert "/no_think" in script
    assert '[Math]::Min(2,' in script


def test_cpu_profile_enforces_windows_affinity_and_lower_priority() -> None:
    script = _script()

    assert "ProcessorAffinity" in script
    assert "ProcessPriorityClass]::BelowNormal" in script
    assert "Get-AffinityMask" in script
    assert "Set-OllamaProcessBudget" in script


def test_cpu_profile_recovers_ollama_before_model_cli_calls() -> None:
    script = _script()

    restart_position = script.index("Restart-OllamaServerSafely `")
    show_position = script.index("& $OllamaExe show $BaseModel")
    create_position = script.index("& $OllamaExe create $TunedModel")
    assert restart_position < show_position < create_position


def test_cpu_profile_is_plan_only_without_apply_and_keeps_backup() -> None:
    script = _script()

    assert "if (-not $Apply)" in script
    assert "No server resources were changed" in script
    assert "argus.env.pre-ollama-cpu-tuning" in script
    assert "previous_machine_environment" in script


def test_cpu_profile_restarts_only_declared_argus_tasks() -> None:
    script = _script()

    assert '[string]$ApiTaskName = "ARGUS-API"' in script
    assert '[string]$WorkerTaskName = "ARGUS-Worker"' in script
    assert "Stop-ScheduledTask -TaskName $ApiTaskName" in script
    assert "Stop-ScheduledTask -TaskName $WorkerTaskName" in script
    assert "GeoAnalyzer" not in script
