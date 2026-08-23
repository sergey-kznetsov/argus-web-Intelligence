import os
import socket
from pathlib import Path

import psycopg
import pytest

from argus.config import Settings
from argus.storage.postgres_migrations import run_postgres_migrations
from argus.worker import CollectionWorker


def postgres_dsn() -> str:
    value = os.getenv("ARGUS_TEST_POSTGRES_DSN", "").strip()
    if not value:
        pytest.skip("ARGUS_TEST_POSTGRES_DSN is not configured")
    return value


@pytest.mark.asyncio
async def test_worker_startup_rolls_back_registration_when_probe_port_is_busy(
    tmp_path: Path,
    monkeypatch,
):
    dsn = postgres_dsn()
    await run_postgres_migrations(dsn)

    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    port = int(blocker.getsockname()[1])

    settings = Settings(
        execution_role="worker",
        storage_backend="postgresql",
        database_dsn=dsn,
        token_file=tmp_path / "token",
        browser_serp_enabled=False,
        worker_lease_seconds=15,
        worker_heartbeat_seconds=1,
    )
    worker = CollectionWorker(settings, probe_port=port)
    original_register = worker.repository.register_worker
    register_calls = 0

    async def tracked_register(worker_id: str, *, metadata=None):
        nonlocal register_calls
        register_calls += 1
        await original_register(worker_id, metadata=metadata)

    monkeypatch.setattr(worker.repository, "register_worker", tracked_register)
    try:
        with pytest.raises(OSError):
            await worker.start()
    finally:
        blocker.close()

    assert register_calls == 0
    assert worker._started is False
    assert worker._heartbeat_task is None
    assert worker._probe_server is None

    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT worker_id FROM argus.worker_instances WHERE worker_id=%s",
            (worker.worker_id,),
        ).fetchone()
    assert row is None
