from pathlib import Path

from argus.capabilities import runtime_capabilities
from argus.config import Settings


ROOT = Path(__file__).resolve().parents[1]


def test_bootstrap_exposes_hybrid_runtime_without_claiming_model_output_as_evidence() -> None:
    capabilities = runtime_capabilities(
        Settings(),
        discovery_providers=["duckduckgo_fast"],
        geocoding_providers=[],
        archive_providers=[],
        map_providers=[],
    )

    assert capabilities["runtimes"] == ["fast", "browser", "agent"]
    assert capabilities["agent_enabled"] is True
    assert capabilities["agent_backend"] == "auto"
    assert capabilities["agent_fallback_order"] == [
        "ollama-recipe",
        "stagehand",
        "browser-use",
    ]
    research = capabilities["research_intelligence"]
    assert research["backend"] == "local_llm_with_deterministic_fallback"
    assert research["model_output_is_evidence"] is False
    assert research["semantic_exact_excerpt_classifier"] is True
    profiles = {
        item["profile_id"]: item for item in capabilities["research_profiles"]
    }
    assert profiles["urban_signals"]["source_families"] == [
        "official_government",
        "public_appeals",
        "housing_utilities",
        "local_forums",
        "local_media",
        "public_communities",
        "general_web",
    ]
    assert profiles["urban_signals"]["public_maps"] == [
        "yandex_maps_web",
        "2gis_web",
        "google_maps_web",
    ]
    assert profiles["test_public_context"]["source_families"] == [
        "official_government",
        "local_media",
    ]


def test_windows_deployment_contains_ollama_cpu_tuning_tool() -> None:
    script = ROOT / "deploy" / "windows" / "tune-ollama-cpu.ps1"
    content = script.read_text(encoding="utf-8")

    assert "OLLAMA_NUM_PARALLEL" in content
    assert "OLLAMA_MAX_LOADED_MODELS" in content
    assert "OLLAMA_MAX_QUEUE" in content
    assert "BelowNormal" in content
    assert "ProcessorAffinity" in content
    assert "argus-qwen3:8b-cpu" in content
