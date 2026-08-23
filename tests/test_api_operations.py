import asyncio
import json
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


def operation_body(consumer: str, analysis_id: str, *, status: str) -> str:
    return json.dumps(
        {
            "request": {"consumer": consumer, "analysis_id": analysis_id},
            "progress_percent": 100 if status == "completed" else 10,
            "stage": status,
            "partial": False,
            "errors": [],
        }
    )


def test_queue_and_collection_operations_endpoints(tmp_path: Path):
    dsn = postgres_dsn()
    asyncio.run(run_postgres_migrations(dsn))
    newer_id = f"api-operations-new-{uuid4()}"
    older_id = f"api-operations-old-{uuid4()}"
    consumer = f"api-operations-{uuid4()}"
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
            VALUES
              (%s, 'queued', %s::jsonb, NOW(), NOW()),
              (%s, 'completed', %s::jsonb,
               NOW() - INTERVAL '1 second', NOW() - INTERVAL '1 second')
            """,
            (
                newer_id,
                operation_body(consumer, "analysis-new", status="queued"),
                older_id,
                operation_body(consumer, "analysis-old", status="completed"),
            ),
        )

    try:
        with TestClient(create_app(settings)) as client:
            headers = auth_headers(settings)
            assert client.get("/v1/operations/queue").status_code == 401
            response = client.get("/v1/operations/queue", headers=headers)
            assert response.status_code == 200
            payload = response.json()
            assert payload["queued"] >= 1
            assert payload["max_active_collections"] == 77
            assert payload["max_active_per_consumer"] == 11
            assert payload["idempotency_window_seconds"] == 86_400
            assert "active_workers" in payload
            assert "active_leases" in payload

            assert client.get("/v1/operations/collections").status_code == 401
            first = client.get(
                "/v1/operations/collections",
                params={"consumer": consumer, "limit": 1},
                headers=headers,
            )
            assert first.status_code == 200
            first_payload = first.json()
            assert [item["collection_id"] for item in first_payload["items"]] == [newer_id]
            assert first_payload["next_cursor"]

            second = client.get(
                "/v1/operations/collections",
                params={
                    "consumer": consumer,
                    "limit": 1,
                    "cursor": first_payload["next_cursor"],
                },
                headers=headers,
            )
            assert second.status_code == 200
            second_payload = second.json()
            assert [item["collection_id"] for item in second_payload["items"]] == [older_id]
            assert second_payload["next_cursor"] is None

            completed = client.get(
                "/v1/operations/collections",
                params={"consumer": consumer, "status": "completed"},
                headers=headers,
            )
            assert completed.status_code == 200
            assert [item["collection_id"] for item in completed.json()["items"]] == [older_id]

            invalid = client.get(
                "/v1/operations/collections",
                params={"cursor": "not-a-valid-cursor%%%"},
                headers=headers,
            )
            assert invalid.status_code == 400
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute(
                "DELETE FROM argus.collections WHERE collection_id IN (%s, %s)",
                (newer_id, older_id),
            )
