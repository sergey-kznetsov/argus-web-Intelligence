from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from argus.web.app import create_web_app
from argus.web.config import WebSettings
from argus.web.profiles import web_test_profiles


class StubApiClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, object | None, dict[str, object] | None]] = []

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def request_json(
        self,
        method: str,
        path: str,
        *,
        json_body: object | None = None,
        params: dict[str, object] | None = None,
    ) -> tuple[int, object]:
        self.calls.append((method, path, json_body, params))
        if method == "POST" and path == "/v1/collections":
            return 202, {"collection_id": "collection-profile", "status": "queued"}
        return 200, {"status": "ok"}


def settings(tmp_path: Path) -> WebSettings:
    token_file = tmp_path / "token"
    token_file.write_text("internal-api-secret\n", encoding="utf-8")
    password_file = tmp_path / "web-password"
    password_file.write_text("operator-secret\n", encoding="utf-8")
    return WebSettings(
        api_url="http://127.0.0.1:8787",
        api_token_file=token_file,
        password_file=password_file,
        username="argus",
    )


def test_profiles_are_request_fixtures_not_source_routes():
    profiles = web_test_profiles()

    assert set(profiles) == {"kraken", "janus", "historical"}
    assert profiles["kraken"]["consumer"] == "kraken.simulation"
    assert "complaints" in profiles["kraken"]["intents"]
    assert profiles["janus"]["consumer"] == "janus.simulation"
    assert "parking_capacity" in profiles["janus"]["intents"]
    assert profiles["historical"]["consumer"] == "historical.simulation"
    assert "historical_context" in profiles["historical"]["intents"]
    for profile in profiles.values():
        assert "source_id" not in profile
        assert "adapter" not in profile
        assert "route" not in profile


def test_web_exposes_authenticated_simulation_profiles(tmp_path: Path):
    stub = StubApiClient()
    with TestClient(create_web_app(settings(tmp_path), api_client=stub)) as client:
        denied = client.get("/api/test-profiles")
        assert denied.status_code == 401

        response = client.get(
            "/api/test-profiles",
            auth=("argus", "operator-secret"),
        )

    assert response.status_code == 200
    assert set(response.json()) == {"kraken", "janus", "historical"}
    assert stub.calls == []


def test_web_forwards_source_pool_and_russian_output_language(tmp_path: Path):
    stub = StubApiClient()
    payload = {
        "consumer": "kraken.simulation",
        "analysis_id": "web-source-pool",
        "territory": {"city": "Пермь", "address": "Комсомольский проспект, 27"},
        "intents": ["reviews", "public_mentions"],
        "constraints": {
            "max_pages": 30,
            "max_depth": 2,
            "output_language": "ru",
            "source_pool_urls": ["https://example.org/local-source"],
        },
        "allow_partial": True,
    }

    with TestClient(create_web_app(settings(tmp_path), api_client=stub)) as client:
        response = client.post(
            "/api/collections",
            auth=("argus", "operator-secret"),
            json=payload,
        )

    assert response.status_code == 202
    _, path, forwarded, _ = stub.calls[-1]
    assert path == "/v1/collections"
    assert isinstance(forwarded, dict)
    assert forwarded["constraints"]["output_language"] == "ru"
    assert forwarded["constraints"]["source_pool_urls"] == [
        "https://example.org/local-source"
    ]
