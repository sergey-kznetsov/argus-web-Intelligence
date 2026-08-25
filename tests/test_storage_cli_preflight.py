from __future__ import annotations

import json
import os

import pytest

from argus.config import Settings
from argus.storage import cli
from argus.storage.postgres_migrations import EXPECTED_SCHEMA_VERSION, run_postgres_migrations


def postgres_dsn() -> str:
    value = os.getenv("ARGUS_TEST_POSTGRES_DSN", "").strip()
    if not value:
        pytest.skip("ARGUS_TEST_POSTGRES_DSN is not configured")
    return value


@pytest.mark.asyncio
async def test_check_validates_required_schema_objects_and_connectivity(monkeypatch, capsys) -> None:
    dsn = postgres_dsn()
    await run_postgres_migrations(dsn)
    settings = Settings(
        storage_backend="postgresql",
        database_dsn=dsn,
        postgres_pool_min_size=0,
        postgres_pool_max_size=2,
        postgres_pool_timeout_seconds=5,
        postgres_pool_max_waiting=4,
    )
    monkeypatch.setattr(cli, "_settings_and_dsn", lambda: (settings, dsn))

    await cli._check()

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert payload["schema"] == "argus"
    assert payload["schema_version"] == EXPECTED_SCHEMA_VERSION
    assert payload["expected_schema_version"] == EXPECTED_SCHEMA_VERSION
    assert payload["backend"] == "postgresql"
    assert payload["required_schema_objects"] is True
    assert payload["database_connectivity"] is True
    assert payload["postgres_pool"]["pool_max"] == 2
