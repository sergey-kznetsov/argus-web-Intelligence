from __future__ import annotations

from pathlib import Path

import pytest

from argus.bootstrap import build_services
from argus.config import Settings


@pytest.mark.asyncio
async def test_bootstrap_uses_public_map_provenance_generic_web_adapter(tmp_path: Path):
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
    try:
        health = await services.registry.health("generic_web")
    finally:
        await services.shutdown()

    provenance = health["public_map_web_provenance"]
    assert provenance["enabled"] is True
    assert provenance["paid_api"] is False
    assert provenance["providers"] == [
        "yandex_maps_web",
        "2gis_web",
        "google_maps_web",
    ]
