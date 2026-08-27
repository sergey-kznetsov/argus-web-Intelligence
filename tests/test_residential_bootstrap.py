from __future__ import annotations

from pathlib import Path

from argus.bootstrap import build_services
from argus.config import Settings


def test_bootstrap_registers_mingkh_residential_source(tmp_path: Path):
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
    source = services.registry.get("mingkh_residential")

    assert source.source_id == "mingkh_residential"
    assert source.intents == {
        "residential_population",
        "residential_premises_count",
    }
    selected = services.registry.for_intents(["residential_population"])
    assert "mingkh_residential" in {item.source_id for item in selected}
