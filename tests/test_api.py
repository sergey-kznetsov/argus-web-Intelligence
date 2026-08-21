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
        assert "duckduckgo_browser" in payload["discovery_providers"]


def test_capabilities_only_lists_configured_map_providers(tmp_path: Path):
    settings = Settings(
        db_path=tmp_path / "db.sqlite",
        token_file=tmp_path / "token",
        overpass_url="https://overpass.example/api/interpreter",
        browser_serp_enabled=False,
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/v1/capabilities", headers=auth_headers(settings))
        assert response.status_code == 200
        payload = response.json()
        assert payload["map_providers"] == ["openstreetmap_overpass"]
        assert payload["discovery_providers"] == []
