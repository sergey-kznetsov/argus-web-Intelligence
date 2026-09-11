from pathlib import Path

import pytest

from argus.bootstrap import build_services
from argus.config import Settings
from argus.crawler.agent.browser_use import BrowserUseAgent
from argus.crawler.agent.fallback import SequentialAgentBackend
from argus.crawler.agent.ollama_recipe import OllamaRecipeAgent
from argus.crawler.agent.stagehand import StagehandAgent
from argus.sources.recipe_web import LifecycleRecipeWebAdapter


def _settings(tmp_path: Path, **updates) -> Settings:
    values = {
        "execution_role": "embedded",
        "storage_backend": "sqlite",
        "db_path": tmp_path / "argus.sqlite",
        "token_file": tmp_path / "token",
        "browser_serp_enabled": False,
        "searxng_url": None,
        "overpass_url": None,
        "nominatim_url": None,
        "wayback_cdx_url": None,
    }
    values.update(updates)
    return Settings(**values)


def _agent(services):
    tracked = services.registry.get("generic_web")
    adapter = getattr(tracked, "_adapter", None)
    assert isinstance(adapter, LifecycleRecipeWebAdapter)
    return adapter.agent


def test_auto_agent_uses_stable_sequential_fallback_order(tmp_path: Path):
    services = build_services(_settings(tmp_path))
    agent = _agent(services)

    assert isinstance(agent, SequentialAgentBackend)
    assert [type(item) for item in agent.backends] == [
        OllamaRecipeAgent,
        StagehandAgent,
        BrowserUseAgent,
    ]
    assert all(item.llm_gate is services.llm_gate for item in agent.backends)


@pytest.mark.parametrize(
    ("backend", "expected"),
    [
        ("ollama-recipe", OllamaRecipeAgent),
        ("stagehand", StagehandAgent),
        ("browser-use", BrowserUseAgent),
    ],
)
def test_explicit_agent_backend_is_wired(tmp_path: Path, backend: str, expected: type):
    services = build_services(_settings(tmp_path, agent_backend=backend))
    assert isinstance(_agent(services), expected)


def test_agent_is_removed_when_llm_is_explicitly_disabled(tmp_path: Path):
    services = build_services(_settings(tmp_path, llm_enabled=False))
    assert _agent(services) is None