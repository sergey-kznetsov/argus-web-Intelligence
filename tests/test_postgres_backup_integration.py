from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from argus.contracts.models import CollectionRecord, CollectionRequest, CollectionStatus, utcnow
from argus.storage.postgres import PostgresRepository
from argus.storage.postgres_backup import backup_argus_schema, restore_argus_schema
from argus.storage.postgres_migrations import (
    EXPECTED_SCHEMA_VERSION,
    current_postgres_schema_version,
    run_postgres_migrations,
)


def postgres_dsn() -> str:
    value = os.getenv("ARGUS_TEST_POSTGRES_DSN", "").strip()
    if not value:
        pytest.skip("ARGUS_TEST_POSTGRES_DSN is not configured")
    return value


def _client_major(binary: str) -> int | None:
    try:
        result = subprocess.run(
            [binary, "--version"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError:
        return None
    match = re.search(r"(\d+)(?:\.\d+)?", result.stdout or result.stderr)
    return int(match.group(1)) if match else None


async def _server_major(dsn: str) -> int:
    connection = await psycopg.AsyncConnection.connect(dsn, autocommit=True)
    try:
        row = await (await connection.execute("SHOW server_version_num")).fetchone()
        assert row is not None
        return int(row[0]) // 10_000
    finally:
        await connection.close()


def _record(collection_id: str) -> CollectionRecord:
    timestamp = utcnow()
    return CollectionRecord(
        collection_id=collection_id,
        request=CollectionRequest(
            consumer="backup-roundtrip-test",
            analysis_id=f"analysis-{uuid4()}",
            territory={"city": "Ижевск"},
            intents=["public_mentions"],
        ),
        status=CollectionStatus.COMPLETED,
        stage="completed",
        created_at=timestamp,
        updated_at=timestamp,
    )


@pytest.mark.asyncio
async def test_real_backup_restore_roundtrip(tmp_path: Path):
    dsn = postgres_dsn()
    pg_dump = shutil.which("pg_dump")
    pg_restore = shutil.which("pg_restore")
    if not pg_dump or not pg_restore:
        pytest.skip("compatible PostgreSQL client tools are not installed")
    server_major = await _server_major(dsn)
    dump_major = _client_major(pg_dump)
    restore_major = _client_major(pg_restore)
    if dump_major is None or restore_major is None:
        pytest.skip("PostgreSQL client version could not be determined")
    if dump_major < server_major or restore_major < server_major:
        pytest.skip(
            f"PostgreSQL client {dump_major}/{restore_major} is older than server {server_major}"
        )

    await run_postgres_migrations(dsn)
    before_id = f"backup-before-{uuid4()}"
    after_id = f"backup-after-{uuid4()}"
    repository = PostgresRepository(dsn, min_size=1, max_size=2, timeout_seconds=10)
    await repository.initialize()
    try:
        await repository.create_collection(_record(before_id))
    finally:
        await repository.close()

    archive = tmp_path / "argus.dump"
    version = await current_postgres_schema_version(dsn)
    assert version == EXPECTED_SCHEMA_VERSION
    backup_argus_schema(
        dsn,
        archive,
        schema_version=version,
        pg_dump_binary=pg_dump,
    )

    repository = PostgresRepository(dsn, min_size=1, max_size=2, timeout_seconds=10)
    await repository.initialize()
    try:
        await repository.create_collection(_record(after_id))
    finally:
        await repository.close()

    restore_argus_schema(
        dsn,
        archive,
        replace_existing_argus=True,
        pg_restore_binary=pg_restore,
    )
    assert await current_postgres_schema_version(dsn) == EXPECTED_SCHEMA_VERSION

    repository = PostgresRepository(dsn, min_size=1, max_size=2, timeout_seconds=10)
    await repository.initialize()
    try:
        assert await repository.get_collection(before_id) is not None
        assert await repository.get_collection(after_id) is None
    finally:
        async with repository._pool.connection() as conn:
            await conn.execute(
                "DELETE FROM argus.collections WHERE collection_id=%s",
                (before_id,),
            )
        await repository.close()
