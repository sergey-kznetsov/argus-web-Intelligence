from pathlib import Path

from fastapi.testclient import TestClient

from argus.api.app import create_app
from argus.config import Settings


def test_health_and_auth(tmp_path: Path):
    settings = Settings(db_path=tmp_path / "db.sqlite", token_file=tmp_path / "token")
    with TestClient(create_app(settings)) as client:
        assert client.get("/v1/health").status_code == 200
        assert client.get("/v1/capabilities").status_code == 401
        token = settings.token_file.read_text().strip()
        response = client.get("/v1/capabilities", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        assert response.json()["storage"] == "sqlite"
