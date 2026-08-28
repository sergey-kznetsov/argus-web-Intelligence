from __future__ import annotations

from pathlib import Path

from argus.bootstrap import build_services
from argus.config import Settings


def _settings(tmp_path: Path, *, sitemap_discovery_enabled: bool = True) -> Settings:
    return Settings(
        execution_role="embedded",
        storage_backend="sqlite",
        db_path=tmp_path / "argus.sqlite",
        token_file=tmp_path / "token",
        browser_serp_enabled=False,
        searxng_url=None,
        overpass_url=None,
        nominatim_url=None,
        wayback_cdx_url=None,
        sitemap_discovery_enabled=sitemap_discovery_enabled,
    )


def test_bootstrap_registers_mingkh_residential_source(tmp_path: Path):
    services = build_services(_settings(tmp_path))
    source = services.registry.get("mingkh_residential")

    assert source.source_id == "mingkh_residential"
    assert source.intents == {
        "residential_population",
        "residential_premises_count",
    }
    selected = services.registry.for_intents(["residential_population"])
    assert "mingkh_residential" in {item.source_id for item in selected}


def test_explicit_residential_sitemap_navigation_remains_registered_when_opportunistic_disabled(
    tmp_path: Path,
):
    services = build_services(_settings(tmp_path, sitemap_discovery_enabled=False))

    site_discovery = services.registry.get("site_discovery")
    generic_web = services.registry.get("generic_web")

    assert site_discovery.source_id == "site_discovery"
    assert generic_web.sitemap_discovery_enabled is False
