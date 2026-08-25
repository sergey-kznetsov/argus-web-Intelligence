from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from argus.api.app import create_app
from argus.config import Settings


def auth_headers(settings: Settings) -> dict[str, str]:
    token = settings.token_file.read_text().strip()
    return {"Authorization": f"Bearer {token}"}


def assert_security_headers(response) -> None:
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["cross-origin-resource-policy"] == "same-origin"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert "camera=()" in response.headers["permissions-policy"]


def test_security_headers_cover_health_auth_and_normal_responses(tmp_path: Path):
    settings = Settings(db_path=tmp_path / "db.sqlite", token_file=tmp_path / "token")

    with TestClient(create_app(settings)) as client:
        health = client.get("/v1/health")
        assert health.status_code == 200
        assert_security_headers(health)

        unauthorized = client.get("/v1/capabilities")
        assert unauthorized.status_code == 401
        assert_security_headers(unauthorized)

        authorized = client.get("/v1/capabilities", headers=auth_headers(settings))
        assert authorized.status_code == 200
        assert_security_headers(authorized)


def test_direct_peer_rate_limit_returns_retry_after_and_security_headers(tmp_path: Path):
    settings = Settings(
        db_path=tmp_path / "db.sqlite",
        token_file=tmp_path / "token",
        api_rate_limit_requests_per_minute=1.0,
        api_rate_limit_burst=2,
    )

    with TestClient(create_app(settings)) as client:
        headers = auth_headers(settings)
        assert client.get("/v1/capabilities", headers=headers).status_code == 200
        assert client.get("/v1/sources", headers=headers).status_code == 200

        limited = client.get(
            "/v1/operations/metrics",
            headers={**headers, "X-Forwarded-For": "203.0.113.99"},
        )
        assert limited.status_code == 429
        assert limited.json() == {"detail": {"code": "CLIENT_RATE_LIMITED"}}
        assert int(limited.headers["retry-after"]) >= 1
        assert_security_headers(limited)

        # Health remains available to the local orchestrator even when the peer bucket
        # is exhausted.
        health = client.get("/v1/health")
        assert health.status_code == 200
        assert_security_headers(health)


def test_metrics_surface_reports_security_posture_without_hostnames(tmp_path: Path):
    settings = Settings(
        db_path=tmp_path / "db.sqlite",
        token_file=tmp_path / "token",
        deny_outbound_hosts=["internal.example", "metadata.example"],
        outbound_public_ports=[80, 443],
    )

    with TestClient(create_app(settings)) as client:
        payload = client.get(
            "/v1/operations/metrics",
            headers=auth_headers(settings),
        ).json()
        security = payload["security"]
        assert security["security_headers"] is True
        assert security["direct_peer_rate_limit"] is True
        assert security["forwarded_client_ip_trusted"] is False
        assert security["public_outbound_ports"] == [80, 443]
        assert security["denied_outbound_host_count"] == 2
        assert "internal.example" not in str(payload)
