from pathlib import Path

from argus.bootstrap import build_services
from argus.config import Settings
from argus.sources.recipe_web import LifecycleRecipeWebAdapter
from argus.storage.lifecycle_sqlite import LifecycleAtomicSQLiteRepository


def test_bootstrap_uses_recipe_lifecycle_layers(tmp_path: Path):
    services = build_services(
        Settings(
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
    )

    assert isinstance(services.repository, LifecycleAtomicSQLiteRepository)
    tracked = services.registry.get("generic_web")
    adapter = getattr(tracked, "_adapter", None)
    assert isinstance(adapter, LifecycleRecipeWebAdapter)
    assert adapter.repository is services.repository
    assert adapter.recipes.repository is services.repository
    assert adapter.recipes.failure_threshold == 3
    assert adapter.recipes.max_age_days == 30
    assert adapter.recipes.keep_versions == 10
