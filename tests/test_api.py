from pathlib import Path

from fastapi.testclient import TestClient

from argus.api.app import create_app
from argus.capabilities import (
    DOCUMENT_EXTRACTORS,
    GEOSPATIAL_EXTRACTORS,
    STRUCTURED_EXTRACTORS,
    VISUAL_EXTRACTORS,
)
from argus.config import Settings
from argus.module_protocol import MODULE_ID


def auth_headers(settings: Settings) -> dict[str, str]:
    token = settings.token_file.read_text().strip()
    return {"Authorization": f"Bearer {token}"}


def test_health_and_auth(tmp_path: Path):
    settings = Settings(db_path=tmp_path / "db.sqlite", token_file=tmp_path / "token")
    with TestClient(create_app(settings)) as client:
        health_response = client.get("/v1/health")
        assert health_response.status_code == 200
        health = health_response.json()
        assert health["protocol_version"] == "1.0.0"
        assert health["module_id"] == MODULE_ID
        assert health["status"] == "ok"
        assert health["checks"]["database"]["backend"] == "sqlite"
        assert client.head("/v1/health").status_code == 200

        assert client.get("/v1/manifest").status_code == 401
        manifest_response = client.get("/v1/manifest", headers=auth_headers(settings))
        assert manifest_response.status_code == 200
        manifest = manifest_response.json()
        assert manifest["protocol_version"] == "1.0.0"
        assert manifest["module_id"] == MODULE_ID
        assert manifest["ui"] == {
            "optional": False,
            "default_enabled": True,
            "analysis_launch_toggle": False,
            "capability_card": False,
        }

        assert client.get("/v1/capabilities").status_code == 401
        response = client.get("/v1/capabilities", headers=auth_headers(settings))
        assert response.status_code == 200
        payload = response.json()
        assert payload["storage"] == "sqlite"
        assert payload["execution_role"] == "embedded"
        assert payload["queue_backend"] == "embedded"
        assert payload["idempotent_submission"] is False
        assert payload["worker_required_for_readiness"] is False
        assert payload["runtimes"] == ["fast", "browser"]
        assert payload["agent_enabled"] is False
        assert payload["agent_backend"] is None
        assert payload["agent_backends"] == []
        assert payload["research_intelligence"]["backend"] == "deterministic"
        assert payload["research_intelligence"]["llm_backend"] is None
        assert payload["unavailable_agent_backends"]["llm-agent"]["status"] == "disabled"
        assert (
            payload["unavailable_agent_backends"]["llm-agent"]["reason_code"]
            == "CRAWLER_ONLY_RUNTIME"
        )
        assert payload["result_delivery"] == {
            "full_result_max_items": 100,
            "full_result_max_bytes": 4 * 1024 * 1024,
            "page_default_size": 50,
            "page_max_size": 100,
            "page_max_bytes": 2 * 1024 * 1024,
            "pagination": "opaque_keyset",
            "paged_results_require_terminal_status": True,
        }
        assert payload["map_providers"] == []
        assert payload["geocoding_providers"] == []
        assert payload["archive_providers"] == []
        assert payload["sitemap_discovery"] is True
        assert payload["structured_extractors"] == list(STRUCTURED_EXTRACTORS)
        assert payload["document_extractors"] == list(DOCUMENT_EXTRACTORS)
        assert payload["visual_extractors"] == list(VISUAL_EXTRACTORS)
        assert payload["geospatial_extractors"] == list(GEOSPATIAL_EXTRACTORS)
        assert payload["historical_timeline"] is True
        assert payload["historical_images"] is True
        assert "duckduckgo_fast" in payload["discovery_providers"]

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
        assert payload["structured_extractors"] == list(STRUCTURED_EXTRACTORS)

        sources = client.get("/v1/sources", headers=auth_headers(settings)).json()
        source_ids = {item["source_id"] for item in sources}
        assert "wayback_cdx" in source_ids
        # The explicit source-owned sitemap adapter remains registered for mandatory
        # source navigation. This setting disables only opportunistic Generic Web expansion.
        assert "site_discovery" in source_ids
