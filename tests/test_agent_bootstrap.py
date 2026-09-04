from pathlib import Path

import pytest

from argus.bootstrap import build_services
from argus.config import Settings
from argus.sources.recipe_web import LifecycleRecipeWebAdapter


@pytest.mark.asyncio
async def test_legacy_agent_configuration_cannot_reenable_llm_runtime(tmp_path: Path):
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
    assert adapter.agent is None
    assert services.llm_health is None
    assert services.llm_required_on_start is False

    health = await adapter.health()
    assert "agent_execution" not in health


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


@pytest.mark.parametrize("legacy_backend", ["browser-use", "stagehand", "ollama-recipe"])
def test_legacy_agent_backends_are_inert(tmp_path: Path, legacy_backend: str):
    settings = Settings(
        execution_role="embedded",
        storage_backend="sqlite",
        db_path=tmp_path / f"{legacy_backend}.sqlite",
        token_file=tmp_path / f"{legacy_backend}.token",
        agent_enabled=True,
        agent_backend=legacy_backend,
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
    assert services.llm_health is None
