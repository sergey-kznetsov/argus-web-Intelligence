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


def payload(consumer: str, analysis_id: str, *, key: str) -> dict[str, object]:
    return {
        "protocol_version": "1.0.0",
        "consumer": consumer,
        "analysis_id": analysis_id,
        "idempotency_key": key,
        "territory": {"city": "Ижевск"},
        "intents": ["public_mentions"],
        "allow_partial": True,
    }


def test_api_applies_consumer_and_global_queue_backpressure(tmp_path: Path):
    dsn = postgres_dsn()
    asyncio.run(run_postgres_migrations(dsn))
    with psycopg.connect(dsn) as conn:
        baseline = int(
            conn.execute(
                "SELECT COUNT(*) FROM argus.collections "
                "WHERE status IN ('queued', 'running')"
            ).fetchone()[0]
        )

    suffix = uuid4().hex
    consumer_a = f"capacity-a-{suffix}"
    consumer_b = f"capacity-b-{suffix}"
    consumer_c = f"capacity-c-{suffix}"
    settings = Settings(
        execution_role="api",
        storage_backend="postgresql",
        database_dsn=dsn,
        token_file=tmp_path / "token",
        browser_serp_enabled=False,
        queue_max_active_collections=baseline + 2,
        queue_max_active_per_consumer=1,
        queue_retry_after_seconds=17,
    )
    created_ids: set[str] = set()
    with TestClient(create_app(settings)) as client:
        headers = auth_headers(settings)
        first_payload = payload(consumer_a, f"analysis-a-{suffix}", key="retry-a")
        first = client.post("/v1/collections", json=first_payload, headers=headers)
        assert first.status_code == 202
        first_id = first.json()["collection_id"]
        created_ids.add(first_id)

        duplicate = client.post("/v1/collections", json=first_payload, headers=headers)
        assert duplicate.status_code == 202
        assert duplicate.json()["collection_id"] == first_id

        consumer_limited = client.post(
            "/v1/collections",
            json=payload(consumer_a, f"analysis-a2-{suffix}", key="retry-a2"),
            headers=headers,
        )
        assert consumer_limited.status_code == 429
        assert consumer_limited.headers["Retry-After"] == "17"
        assert consumer_limited.json()["detail"]["scope"] == "consumer"

        second = client.post(
            "/v1/collections",
            json=payload(consumer_b, f"analysis-b-{suffix}", key="retry-b"),
            headers=headers,
        )
        assert second.status_code == 202
        created_ids.add(second.json()["collection_id"])

        globally_limited = client.post(
            "/v1/collections",
            json=payload(consumer_c, f"analysis-c-{suffix}", key="retry-c"),
            headers=headers,
        )
        assert globally_limited.status_code == 503
        assert globally_limited.headers["Retry-After"] == "17"
        assert globally_limited.json()["detail"]["scope"] == "global"

    with psycopg.connect(dsn) as conn:
        if created_ids:
            conn.execute(
                "DELETE FROM argus.collections WHERE collection_id = ANY(%s)",
                (list(created_ids),),
            )
