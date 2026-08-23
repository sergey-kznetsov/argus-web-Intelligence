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


def test_server_api_readiness_requires_live_worker(tmp_path: Path):
    dsn = postgres_dsn()
    asyncio.run(run_postgres_migrations(dsn))
    with psycopg.connect(dsn) as conn:
        conn.execute("DELETE FROM argus.worker_instances")

    settings = Settings(
        execution_role="api",
        storage_backend="postgresql",
        database_dsn=dsn,
        token_file=tmp_path / "token",
        browser_serp_enabled=False,
        worker_health_max_age_seconds=60,
    )
    worker_id = f"readiness-{uuid4()}"
    with TestClient(create_app(settings)) as client:
        response = client.get("/v1/health")
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "degraded"
        assert payload["execution_role"] == "api"
        assert payload["checks"]["database"]["status"] == "ok"
        assert payload["checks"]["worker"]["active_workers"] == 0
        assert client.head("/v1/health").status_code == 503

        with psycopg.connect(dsn) as conn:
            conn.execute(
                """
                INSERT INTO argus.worker_instances(worker_id, started_at, heartbeat_at, metadata)
                VALUES(%s, NOW(), NOW(), '{}'::jsonb)
                """,
                (worker_id,),
            )

        response = client.get("/v1/health")
        payload = response.json()
        assert payload["status"] == "ok"
        assert payload["checks"]["worker"]["status"] == "ok"
        assert payload["checks"]["worker"]["active_workers"] >= 1
        assert client.head("/v1/health").status_code == 200

    with psycopg.connect(dsn) as conn:
        conn.execute("DELETE FROM argus.worker_instances WHERE worker_id=%s", (worker_id,))
