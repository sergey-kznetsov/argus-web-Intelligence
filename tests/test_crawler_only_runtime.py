from pathlib import Path

from argus.capabilities import runtime_capabilities
from argus.config import Settings


ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "src" / "argus" / "bootstrap.py"


def test_production_bootstrap_has_no_ollama_runtime_wiring() -> None:
    bootstrap = BOOTSTRAP.read_text(encoding="utf-8")

    assert "Ollama" not in bootstrap
    assert "llm_health=None" in bootstrap
    assert "llm_required_on_start=False" in bootstrap
    assert "agent=None" in bootstrap
    assert "intent_evidence_classifier=None" in bootstrap
    assert "HeuristicResearchPlanner" in bootstrap
    assert "EvidenceAwareHeuristicFollowupResearchPlanner" in bootstrap
    assert "HeuristicResearchSupervisor" in bootstrap


def test_capabilities_advertise_fast_browser_crawler_without_llm() -> None:
    capabilities = runtime_capabilities(
        Settings(),
        discovery_providers=["duckduckgo_fast"],
        geocoding_providers=[],
        archive_providers=[],
        map_providers=[],
    )

    assert capabilities["runtimes"] == ["fast", "browser"]
    assert capabilities["agent_enabled"] is False
    assert capabilities["agent_backend"] is None
    assert capabilities["agent_backends"] == []

    research = capabilities["research_intelligence"]
    assert isinstance(research, dict)
    assert research["backend"] == "deterministic"
    assert research["llm_backend"] is None
    assert research["model"] is None
    assert research["recursive_followups"] is True
    assert research["consumer_domain_interpretation"] is True


def test_windows_deployment_has_no_ollama_tuning_tool() -> None:
    assert not (ROOT / "deploy" / "windows" / "tune-ollama-cpu.ps1").exists()
