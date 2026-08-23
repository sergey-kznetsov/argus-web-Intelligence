import asyncio
import os
from pathlib import Path
from uuid import uuid4

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


def test_queue_operations_endpoint_reports_server_state(tmp_path: Path):
    dsn = postgres_dsn()
    asyncio.run(run_postgres_migrations(dsn))
    collection_id = f"api-operations-{uuid4()}"
    settings = Settings(
        execution_role="api",
        storage_backend="postgresql",
        database_dsn=dsn,
        token_file=tmp_path / "token",
        browser_serp_enabled=False,
        queue_max_active_collections=77,
        queue_max_active_per_consumer=11,
        idempotency_window_seconds=86_400,
    )
    with psycopg.connect(dsn) as conn:
        conn.execute(
            """
            INSERT INTO argus.collections(collection_id, status, body, created_at, updated_at)
            VALUES(%s, 'queued', %s::jsonb, NOW(), NOW())
            """,
            (
                collection_id,
                '{"request":{"consumer":"api-operations"}}',
            ),
        )

    try:
        with TestClient(create_app(settings)) as client:
            assert client.get("/v1/operations/queue").status_code == 401
            response = client.get(
                "/v1/operations/queue",
                headers=auth_headers(settings),
            )
            assert response.status_code == 200
            payload = response.json()
            assert payload["queued"] >= 1
            assert payload["max_active_collections"] == 77
            assert payload["max_active_per_consumer"] == 11
            assert payload["idempotency_window_seconds"] == 86_400
            assert "active_workers" in payload
            assert "active_leases" in payload
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute(
                "DELETE FROM argus.collections WHERE collection_id=%s",
                (collection_id,),
            )
