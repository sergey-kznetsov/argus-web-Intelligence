from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import psycopg
import pytest
from psycopg_pool import TooManyRequests

import argus.storage.postgres_migrations as migration_module
from argus.contracts.models import CollectionRecord, CollectionRequest, CollectionStatus, utcnow
from argus.storage.postgres import PostgresRepository
from argus.storage.postgres_migrations import PostgresMigration


def postgres_dsn() -> str:
    value = os.getenv("ARGUS_TEST_POSTGRES_DSN", "").strip()
    if not value:
        pytest.skip("ARGUS_TEST_POSTGRES_DSN is not configured")
    return value


def collection(collection_id: str) -> CollectionRecord:
    timestamp = utcnow()
    return CollectionRecord(
        collection_id=collection_id,
        request=CollectionRequest(
            consumer="operational-hardening-test",
            analysis_id=f"analysis-{uuid4()}",
            territory={"city": "Ижевск"},
            intents=["public_mentions"],
        ),
        status=CollectionStatus.QUEUED,
        created_at=timestamp,
        updated_at=timestamp,
    )


@pytest.mark.asyncio
async def test_failed_migration_rolls_back_schema_and_version(monkeypatch):
    dsn = postgres_dsn()
    await migration_module.run_postgres_migrations(dsn)
    base_version = await migration_module.current_postgres_schema_version(dsn)
    probe_table = f"migration_failure_probe_{uuid4().hex}"
    failing = PostgresMigration(
        version=base_version + 1,
        name="intentional_test_failure",
        statements=(
            f"CREATE TABLE argus.{probe_table}(id INTEGER)",
            "SELECT argus_function_that_does_not_exist()",
        ),
    )
    monkeypatch.setattr(
        migration_module,
        "MIGRATIONS",
        (*migration_module.MIGRATIONS, failing),
    )

    with pytest.raises(psycopg.Error):
        await migration_module.run_postgres_migrations(dsn)

    assert await migration_module.current_postgres_schema_version(dsn) == base_version
    connection = await psycopg.AsyncConnection.connect(dsn, autocommit=True)
    try:
        row = await (
            await connection.execute(
                "SELECT to_regclass(%s)",
                (f"argus.{probe_table}",),
            )
        ).fetchone()
        assert row is not None
        assert row[0] is None
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_independent_workers_claim_distinct_collections_concurrently():
    dsn = postgres_dsn()
    await migration_module.run_postgres_migrations(dsn)
    repositories = [
        PostgresRepository(dsn, min_size=0, max_size=2, timeout_seconds=5, max_waiting=4)
        for _ in range(4)
    ]
    workers = [f"worker-{uuid4()}" for _ in repositories]
    collection_ids = [f"multiworker-{uuid4()}" for _ in repositories]
    for repository in repositories:
        await repository.initialize()
    try:
        for collection_id in collection_ids:
            await repositories[0].create_collection(collection(collection_id))
        for repository, worker_id in zip(repositories, workers, strict=True):
            await repository.register_worker(worker_id, metadata={"test": True})

        claimed = await asyncio.gather(
            *(
                repository.claim_next_collection(worker_id, lease_seconds=30)
                for repository, worker_id in zip(repositories, workers, strict=True)
            )
        )

        assert None not in claimed
        assert len(set(claimed)) == len(claimed)
        assert set(claimed) == set(collection_ids)
    finally:
        for repository, worker_id in zip(repositories, workers, strict=True):
            for collection_id in collection_ids:
                await repository.release_collection_lease(collection_id, worker_id)
            await repository.unregister_worker(worker_id)
            await repository.close()


@pytest.mark.asyncio
async def test_pool_wait_queue_is_bounded_under_saturation():
    dsn = postgres_dsn()
    await migration_module.run_postgres_migrations(dsn)
    repository = PostgresRepository(
        dsn,
        min_size=1,
        max_size=1,
        timeout_seconds=5,
        max_waiting=1,
    )
    await repository.initialize()
    held = await repository._pool.getconn()
    waiter = asyncio.create_task(repository._pool.getconn(timeout=2))
    try:
        for _ in range(50):
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
