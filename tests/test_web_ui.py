from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from argus.web.app import create_web_app
from argus.web.config import WebSettings


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
        if path == "/v1/health":
            return 200, {"status": "ok"}
        if method == "POST" and path == "/v1/collections":
            return 202, {"collection_id": "collection-1", "status": "queued"}
        return 200, {"status": "completed"}


def web_settings(tmp_path: Path) -> WebSettings:
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


def test_web_ui_requires_auth_and_never_embeds_internal_bearer(tmp_path: Path):
    settings = web_settings(tmp_path)
    stub = StubApiClient()

    with TestClient(create_web_app(settings, api_client=stub)) as client:
        denied = client.get("/")
        assert denied.status_code == 401
        assert "Basic" in denied.headers["www-authenticate"]

        response = client.get("/", auth=("argus", "operator-secret"))
        assert response.status_code == 200
        assert "ARGUS Web Intelligence" in response.text
        assert "internal-api-secret" not in response.text
        assert response.headers["x-frame-options"] == "DENY"
        assert "default-src 'self'" in response.headers["content-security-policy"]


def test_web_ui_submits_collection_through_fixed_local_api_surface(tmp_path: Path):
    settings = web_settings(tmp_path)
    stub = StubApiClient()
    payload = {
        "consumer": "standalone.web",
        "analysis_id": "web-test",
        "territory": {"city": "Ижевск", "address": "Пушкинская, 277"},
        "intents": ["reviews", "local_news"],
        "constraints": {"max_pages": 20, "max_depth": 2},
        "allow_partial": True,
    }

    with TestClient(create_web_app(settings, api_client=stub)) as client:
        response = client.post(
            "/api/collections",
            auth=("argus", "operator-secret"),
            json=payload,
        )

    assert response.status_code == 202
    assert response.json()["collection_id"] == "collection-1"
    method, path, forwarded, params = stub.calls[-1]
    assert method == "POST"
    assert path == "/v1/collections"
    assert params is None
    assert isinstance(forwarded, dict)
    assert forwarded["consumer"] == "standalone.web"
    assert forwarded["intents"] == ["reviews", "local_news"]


def test_web_ui_rejects_remote_backend_targets():
    try:
        WebSettings(api_url="https://example.com")
    except ValueError as exc:
        assert "local ARGUS API" in str(exc)
    else:
        raise AssertionError("remote ARGUS API target must be rejected")
