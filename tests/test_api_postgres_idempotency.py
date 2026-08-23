import asyncio
import os
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from argus.api.app import create_app
from argus.config import Settings
from argus.storage.postgres_migrations import run_postgres_migrations


def postgres_dsn() -> str:
    value = os.getenv("ARGUS_TEST_POSTGRES_DSN", "").strip()
    if not value:
        pytest.skip("ARGUS_TEST_POSTGRES_DSN is not configured")
    return value


def auth_headers(settings: Settings) -> dict[str, str]:
    token = settings.token_file.read_text(encoding="utf-8").strip()
    return {"Authorization": f"Bearer {token}"}


def base_payload() -> dict[str, object]:
    return {
        "protocol_version": "1.0.0",
        "consumer": "kraken",
        "analysis_id": "analysis-idempotency-test",
        "territory": {"city": "Ижевск", "address": "Пушкинская, 277"},
        "intents": ["public_mentions"],
        "constraints": {"max_pages": 10, "max_depth": 1},
        "allow_partial": True,
    }


def test_server_api_collection_submission_is_idempotent(tmp_path: Path):
    dsn = postgres_dsn()
    asyncio.run(run_postgres_migrations(dsn))
    settings = Settings(
        execution_role="api",
        storage_backend="postgresql",
        database_dsn=dsn,
        token_file=tmp_path / "token",
        browser_serp_enabled=False,
    )
    created_ids: set[str] = set()
    with TestClient(create_app(settings)) as client:
        headers = auth_headers(settings)

        explicit = base_payload()
        explicit["idempotency_key"] = "kraken-retry-1"
        first = client.post("/v1/collections", json=explicit, headers=headers)
        second = client.post("/v1/collections", json=explicit, headers=headers)
        assert first.status_code == 202
        assert second.status_code == 202
        first_id = first.json()["collection_id"]
        created_ids.add(first_id)
        assert second.json()["collection_id"] == first_id

        conflict = base_payload()
        conflict["idempotency_key"] = "kraken-retry-1"
        conflict["intents"] = ["local_news"]
        response = client.post("/v1/collections", json=conflict, headers=headers)
        assert response.status_code == 409

        automatic = base_payload()
        automatic["analysis_id"] = "analysis-auto-idempotency"
        auto_first = client.post("/v1/collections", json=automatic, headers=headers)
        auto_second = client.post("/v1/collections", json=automatic, headers=headers)
        assert auto_first.status_code == 202
        assert auto_second.status_code == 202
        auto_id = auto_first.json()["collection_id"]
        created_ids.add(auto_id)
        assert auto_second.json()["collection_id"] == auto_id

        other_consumer = base_payload()
        other_consumer["consumer"] = "janus"
        other_consumer["idempotency_key"] = "kraken-retry-1"
        other = client.post("/v1/collections", json=other_consumer, headers=headers)
        assert other.status_code == 202
        other_id = other.json()["collection_id"]
        created_ids.add(other_id)
        assert other_id != first_id

    if created_ids:
        with psycopg.connect(dsn) as conn:
            conn.execute(
                "DELETE FROM argus.collections WHERE collection_id = ANY(%s)",
                (list(created_ids),),
            )
