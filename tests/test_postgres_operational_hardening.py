from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import psycopg
import pytest
from psycopg_pool import TooManyRequests

from argus.contracts.models import (
    CollectionRecord,
    CollectionRequest,
    CollectionStatus,
    utcnow,
)
from argus.storage import postgres_migrations as migration_module
from argus.storage.postgres import PostgresRepository


def postgres_dsn() -> str:
    dsn = os.getenv("ARGUS_TEST_POSTGRES_DSN", "").strip()
    if not dsn:
        pytest.skip("ARGUS_TEST_POSTGRES_DSN is not configured")
    return dsn


def collection(collection_id: str) -> CollectionRecord:
    now = utcnow()
    return CollectionRecord(
        collection_id=collection_id,
        request=CollectionRequest(
            consumer="postgres-operational-test",
            analysis_id=f"analysis-{collection_id}",
            territory={"city": "Ижевск"},
            intents=["public_mentions"],
        ),
        status=CollectionStatus.QUEUED,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_postgres_queue_indexes_exist():
    dsn = postgres_dsn()
    await migration_module.run_postgres_migrations(dsn)
    connection = await psycopg.AsyncConnection.connect(dsn, autocommit=True)
    try:
        rows = await (
            await connection.execute(
                """
                SELECT indexname
                FROM pg_indexes
                WHERE schemaname='argus'
                """
            )
        ).fetchall()
        names = {str(row[0]) for row in rows}
        assert "ix_argus_collections_active_fifo" in names
        assert "ix_argus_collections_terminal_updated" in names
        assert "ix_argus_snapshots_retention" in names
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_postgres_repository_exposes_pool_limits_and_health():
    dsn = postgres_dsn()
    await migration_module.run_postgres_migrations(dsn)
    repository = PostgresRepository(
        dsn,
        min_size=1,
        max_size=3,
        timeout_seconds=5,
        max_waiting=7,
    )
    await repository.initialize()
    try:
        stats = repository.pool_stats()
        assert stats["min_size"] == 1
        assert stats["max_size"] == 3
        assert stats["max_waiting"] == 7
        assert stats["requests_waiting"] == 0
        health = await repository.health()
        assert health["status"] == "ok"
        assert health["schema_version"] == migration_module.EXPECTED_SCHEMA_VERSION
        assert health["pool"]["max_size"] == 3
        assert health["pool"]["max_waiting"] == 7
    finally:
        await repository.close()


@pytest.mark.asyncio
async def test_pool_exhaustion_is_bounded_and_recovers():
    dsn = postgres_dsn()
    await migration_module.run_postgres_migrations(dsn)
    repository = PostgresRepository(
        dsn,
        min_size=1,
        max_size=1,
        timeout_seconds=2,
        max_waiting=1,
    )
    await repository.initialize()
    held = await repository._pool.getconn()
    waiter = asyncio.create_task(repository._pool.getconn(timeout=2))
    try:
        for _ in range(100):
            if repository.pool_stats()["requests_waiting"] >= 1:
                break
            await asyncio.sleep(0.01)
        assert repository.pool_stats()["requests_waiting"] == 1
        assert repository.pool_stats()["max_waiting"] == 1

        with pytest.raises(TooManyRequests):
            await repository._pool.getconn(timeout=1)
    finally:
        await repository._pool.putconn(held)
        waiting_connection = await waiter
        await repository._pool.putconn(waiting_connection)
        await repository.close()


@pytest.mark.asyncio
async def test_pool_recovers_after_postgresql_backend_is_terminated():
    dsn = postgres_dsn()
    await migration_module.run_postgres_migrations(dsn)
    repository = PostgresRepository(
        dsn,
        min_size=1,
        max_size=2,
        timeout_seconds=5,
        max_waiting=4,
    )
    await repository.initialize()
    held = await repository._pool.getconn()
    terminator = await psycopg.AsyncConnection.connect(dsn, autocommit=True)
    try:
        pid_row = await (await held.execute("SELECT pg_backend_pid()" )).fetchone()
        assert pid_row is not None
        backend_pid = int(pid_row["pg_backend_pid"])
        terminated = await (
            await terminator.execute("SELECT pg_terminate_backend(%s)", (backend_pid,))
        ).fetchone()
        assert terminated is not None
        assert bool(terminated[0]) is True

        with pytest.raises(psycopg.Error):
            await held.execute("SELECT 1")
        await repository._pool.putconn(held)
        held = None

        await repository._pool.check()
        health = {"status": "error"}
        for _ in range(50):
            health = await repository.health()
            if health.get("status") == "ok":
                break
            await asyncio.sleep(0.05)
        assert health["status"] == "ok"
        assert health["schema_version"] == migration_module.EXPECTED_SCHEMA_VERSION
    finally:
        if held is not None:
            await repository._pool.putconn(held)
        await terminator.close()
        await repository.close()
