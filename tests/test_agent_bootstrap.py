from pathlib import Path

import pytest

from argus.bootstrap import build_services
from argus.config import Settings
from argus.crawler.agent.ollama_recipe import OllamaRecipeAgent
from argus.sources.recipe_web import LifecycleRecipeWebAdapter


@pytest.mark.asyncio
async def test_ollama_recipe_agent_is_optional_last_resort_with_bounded_health_contract(
    tmp_path: Path,
):
    settings = Settings(
        execution_role="embedded",
        storage_backend="sqlite",
        db_path=tmp_path / "argus.sqlite",
        token_file=tmp_path / "token",
        agent_enabled=True,
        agent_backend="ollama-recipe",
        browser_serp_enabled=False,
        searxng_url=None,
        overpass_url=None,
        nominatim_url=None,
        wayback_cdx_url=None,
    )

    services = build_services(settings)
    tracked = services.registry.get("generic_web")
    adapter = getattr(tracked, "_adapter", None)

    assert isinstance(adapter, LifecycleRecipeWebAdapter)
    assert isinstance(adapter.agent, OllamaRecipeAgent)

    health = await adapter.health()
    agent = health["agent_execution"]
    assert agent["backend"] == "ollama-recipe"
    assert agent["last_resort"] is True
    assert agent["agent_output_is_evidence"] is False
    assert agent["successful_action_paths_require_verified_recipe"] is True
    assert agent["max_direct_replay_urls"] == 2
    assert agent["max_steps"] == 1
    assert agent["max_actions"] == 6
    assert agent["max_visited_urls"] == 8


@pytest.mark.asyncio
async def test_agent_is_disabled_by_default(tmp_path: Path):
    settings = Settings(
        execution_role="embedded",
        storage_backend="sqlite",
        db_path=tmp_path / "argus.sqlite",
        token_file=tmp_path / "token",
        browser_serp_enabled=False,
        searxng_url=None,
        overpass_url=None,
        nominatim_url=None,
        wayback_cdx_url=None,
    )

    services = build_services(settings)
    tracked = services.registry.get("generic_web")
    adapter = getattr(tracked, "_adapter", None)

    assert isinstance(adapter, LifecycleRecipeWebAdapter)
    assert adapter.agent is None
    health = await adapter.health()
    assert "agent_execution" not in health


def test_unavailable_browser_use_backend_fails_fast(tmp_path: Path):
    settings = Settings(
        execution_role="embedded",
        storage_backend="sqlite",
        db_path=tmp_path / "argus.sqlite",
        token_file=tmp_path / "token",
        agent_enabled=True,
        agent_backend="browser-use",
        browser_serp_enabled=False,
        searxng_url=None,
        overpass_url=None,
        nominatim_url=None,
        wayback_cdx_url=None,
    )

    with pytest.raises(RuntimeError, match="browser-use.*not operational"):
        build_services(settings)


def test_unavailable_stagehand_backend_fails_fast(tmp_path: Path):
    settings = Settings(
        execution_role="embedded",
        storage_backend="sqlite",
        db_path=tmp_path / "argus.sqlite",
        token_file=tmp_path / "token",
        agent_enabled=True,
        agent_backend="stagehand",
        browser_serp_enabled=False,
        searxng_url=None,
        overpass_url=None,
        nominatim_url=None,
        wayback_cdx_url=None,
    )

    with pytest.raises(RuntimeError, match="stagehand.*not operational"):
        build_services(settings)
