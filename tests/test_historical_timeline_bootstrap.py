from pathlib import Path

from argus.bootstrap import build_services
from argus.config import Settings
from argus.sources.historical_web import HistoricalTimelineWebAdapter
from argus.sources.recipe_web import LifecycleRecipeWebAdapter


def test_bootstrap_uses_historical_timeline_web_adapter(tmp_path: Path):
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

    assert isinstance(adapter, HistoricalTimelineWebAdapter)
    assert isinstance(adapter, LifecycleRecipeWebAdapter)
    assert adapter.historical_timeline.version == "historical-timeline/1"
