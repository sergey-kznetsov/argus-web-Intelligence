from pathlib import Path

from fastapi.testclient import TestClient

from argus.api.app import create_app
from argus.config import Settings


def auth_headers(settings: Settings) -> dict[str, str]:
    token = settings.token_file.read_text().strip()
    return {"Authorization": f"Bearer {token}"}


def test_health_and_auth(tmp_path: Path):
    settings = Settings(db_path=tmp_path / "db.sqlite", token_file=tmp_path / "token")
    with TestClient(create_app(settings)) as client:
        assert client.get("/v1/health").status_code == 200
        assert client.get("/v1/capabilities").status_code == 401
        response = client.get("/v1/capabilities", headers=auth_headers(settings))
        assert response.status_code == 200
        payload = response.json()
        assert payload["storage"] == "sqlite"
        assert payload["map_providers"] == []
        assert payload["geocoding_providers"] == []
        assert payload["archive_providers"] == []
        assert payload["sitemap_discovery"] is True
        assert payload["structured_extractors"] == ["json_ld"]
        assert "duckduckgo_browser" in payload["discovery_providers"]

        sources = client.get("/v1/sources", headers=auth_headers(settings)).json()
        source_ids = {item["source_id"] for item in sources}
        assert "site_discovery" in source_ids

        source_health = client.get(
            "/v1/sources/generic_web/health",
            headers=auth_headers(settings),
        )
        assert source_health.status_code == 200
        health_payload = source_health.json()
        assert health_payload["status"] == "ready"
        assert health_payload["adapter_status"] == "ok"
        assert health_payload["operational"]["last_attempt_at"] is None


def test_capabilities_only_lists_configured_optional_providers(tmp_path: Path):
    settings = Settings(
        db_path=tmp_path / "db.sqlite",
        token_file=tmp_path / "token",
        overpass_url="https://overpass.example/api/interpreter",
        nominatim_url="https://nominatim.example",
        wayback_cdx_url="https://web.archive.org/cdx/search/cdx",
        browser_serp_enabled=False,
        sitemap_discovery_enabled=False,
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/v1/capabilities", headers=auth_headers(settings))
        assert response.status_code == 200
        payload = response.json()
        assert payload["map_providers"] == ["openstreetmap_overpass"]
        assert payload["geocoding_providers"] == ["nominatim"]
        assert payload["archive_providers"] == ["wayback_cdx"]
        assert payload["discovery_providers"] == []
        assert payload["sitemap_discovery"] is False
        assert payload["structured_extractors"] == ["json_ld"]

        sources = client.get("/v1/sources", headers=auth_headers(settings)).json()
        source_ids = {item["source_id"] for item in sources}
        assert "wayback_cdx" in source_ids
        assert "site_discovery" not in source_ids
