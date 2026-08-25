from __future__ import annotations

import hashlib
from dataclasses import dataclass

import psycopg

_SCHEMA = "argus"
_MIGRATION_LOCK_KEY = 0x4152475553574542  # Stable signed-safe bigint: "ARGUSWEB".


@dataclass(frozen=True, slots=True)
class PostgresMigration:
    version: int
    name: str
    statements: tuple[str, ...]

    @property
    def checksum(self) -> str:
        payload = "\n-- statement --\n".join(statement.strip() for statement in self.statements)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


_BOOTSTRAP_STATEMENTS = (
    f"CREATE SCHEMA IF NOT EXISTS {_SCHEMA}",
    f"""
    CREATE TABLE IF NOT EXISTS {_SCHEMA}.schema_migrations (
      version INTEGER PRIMARY KEY,
      name TEXT NOT NULL,
      checksum TEXT NOT NULL,
      applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
)


MIGRATIONS: tuple[PostgresMigration, ...] = (
    PostgresMigration(
        version=1,
        name="initial_storage",
        statements=(
            f"""
            CREATE TABLE IF NOT EXISTS {_SCHEMA}.collections (
              collection_id TEXT PRIMARY KEY,
              status TEXT NOT NULL,
              body JSONB NOT NULL,
              created_at TIMESTAMPTZ NOT NULL,
              updated_at TIMESTAMPTZ NOT NULL
            )
            """,
            f"CREATE INDEX IF NOT EXISTS ix_argus_collections_status "
            f"ON {_SCHEMA}.collections(status)",
            f"""
            CREATE TABLE IF NOT EXISTS {_SCHEMA}.observations (
              observation_id TEXT PRIMARY KEY,
              collection_id TEXT NOT NULL
                REFERENCES {_SCHEMA}.collections(collection_id) ON DELETE CASCADE,
              body JSONB NOT NULL
            )
            """,
            f"CREATE INDEX IF NOT EXISTS ix_argus_observations_collection "
            f"ON {_SCHEMA}.observations(collection_id)",
            f"""
            CREATE TABLE IF NOT EXISTS {_SCHEMA}.evidence (
              evidence_id TEXT PRIMARY KEY,
              collection_id TEXT NOT NULL
                REFERENCES {_SCHEMA}.collections(collection_id) ON DELETE CASCADE,
              body JSONB NOT NULL
            )
            """,
            f"CREATE INDEX IF NOT EXISTS ix_argus_evidence_collection "
            f"ON {_SCHEMA}.evidence(collection_id)",
            f"""
            CREATE TABLE IF NOT EXISTS {_SCHEMA}.snapshots (
              snapshot_id TEXT PRIMARY KEY,
              source_url TEXT NOT NULL,
              collected_at TIMESTAMPTZ NOT NULL,
              body JSONB NOT NULL
            )
            """,
            f"CREATE INDEX IF NOT EXISTS ix_argus_snapshots_url_time "
            f"ON {_SCHEMA}.snapshots(source_url, collected_at DESC)",
            f"""
            CREATE TABLE IF NOT EXISTS {_SCHEMA}.site_recipes (
              recipe_id TEXT PRIMARY KEY,
              domain TEXT NOT NULL,
              goal TEXT NOT NULL,
              version INTEGER NOT NULL,
              body JSONB NOT NULL,
              UNIQUE(domain, goal, version)
            )
            """,
            f"CREATE INDEX IF NOT EXISTS ix_argus_recipes_lookup "
            f"ON {_SCHEMA}.site_recipes(domain, goal, version DESC)",
        ),
    ),
    PostgresMigration(
        version=2,
        name="worker_leases",
        statements=(
            f"""
            CREATE TABLE IF NOT EXISTS {_SCHEMA}.collection_leases (
              collection_id TEXT PRIMARY KEY
                REFERENCES {_SCHEMA}.collections(collection_id) ON DELETE CASCADE,
              worker_id TEXT NOT NULL,
              leased_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              lease_until TIMESTAMPTZ NOT NULL
            )
            """,
            f"CREATE INDEX IF NOT EXISTS ix_argus_collection_leases_until "
            f"ON {_SCHEMA}.collection_leases(lease_until)",
            f"""
            CREATE TABLE IF NOT EXISTS {_SCHEMA}.worker_instances (
              worker_id TEXT PRIMARY KEY,
              started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb
            )
            """,
            f"CREATE INDEX IF NOT EXISTS ix_argus_worker_instances_heartbeat "
            f"ON {_SCHEMA}.worker_instances(heartbeat_at DESC)",
        ),
    ),
    PostgresMigration(
        version=3,
        name="collection_idempotency",
        statements=(
            f"""
            CREATE TABLE IF NOT EXISTS {_SCHEMA}.collection_idempotency (
              idempotency_key TEXT PRIMARY KEY,
              collection_id TEXT NOT NULL UNIQUE
                REFERENCES {_SCHEMA}.collections(collection_id) ON DELETE CASCADE,
              request_hash TEXT NOT NULL,
              created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            f"CREATE INDEX IF NOT EXISTS ix_argus_collection_idempotency_created "
            f"ON {_SCHEMA}.collection_idempotency(created_at DESC)",
        ),
    ),
    PostgresMigration(
        version=4,
        name="queue_retention_indexes",
        statements=(
            f"""
            CREATE INDEX IF NOT EXISTS ix_argus_collections_active_fifo
            ON {_SCHEMA}.collections(created_at ASC, collection_id ASC)
            WHERE status IN ('queued', 'running')
            """,
            f"""
            CREATE INDEX IF NOT EXISTS ix_argus_collections_terminal_updated
            ON {_SCHEMA}.collections(updated_at ASC, collection_id ASC)
            WHERE status IN ('completed', 'partial', 'blocked', 'failed', 'cancelled')
            """,
            f"""
            CREATE INDEX IF NOT EXISTS ix_argus_snapshots_retention
            ON {_SCHEMA}.snapshots(source_url, collected_at DESC, snapshot_id DESC)
            """,
        ),
    ),
    PostgresMigration(
        version=5,
        name="collection_operations_indexes",
        statements=(
            f"""
            CREATE INDEX IF NOT EXISTS ix_argus_collections_created
            ON {_SCHEMA}.collections(created_at DESC, collection_id DESC)
            """,
            f"""
            CREATE INDEX IF NOT EXISTS ix_argus_collections_status_created
            ON {_SCHEMA}.collections(status, created_at DESC, collection_id DESC)
            """,
            f"""
            CREATE INDEX IF NOT EXISTS ix_argus_collections_consumer_created
            ON {_SCHEMA}.collections(
              (body #>> '{{request,consumer}}'), created_at DESC, collection_id DESC
            )
            """,
        ),
    ),
    PostgresMigration(
        version=6,
        name="result_pagination_indexes",
        statements=(
            f"""
            CREATE INDEX IF NOT EXISTS ix_argus_observations_collection_id
            ON {_SCHEMA}.observations(collection_id, observation_id ASC)
            """,
            f"""
            CREATE INDEX IF NOT EXISTS ix_argus_evidence_collection_id
            ON {_SCHEMA}.evidence(collection_id, evidence_id ASC)
            """,
        ),
    ),
    PostgresMigration(
        version=7,
        name="result_access_retention_grace",
        statements=(
            f"""
            CREATE TABLE IF NOT EXISTS {_SCHEMA}.collection_result_access (
              collection_id TEXT PRIMARY KEY
                REFERENCES {_SCHEMA}.collections(collection_id) ON DELETE CASCADE,
              last_accessed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            f"""
            CREATE INDEX IF NOT EXISTS ix_argus_collection_result_access_time
            ON {_SCHEMA}.collection_result_access(last_accessed_at DESC)
            """,
        ),
    ),
    PostgresMigration(
        version=8,
        name="observation_content_identity_index",
        statements=(
            f"""
            CREATE INDEX IF NOT EXISTS ix_argus_observations_content_identity
            ON {_SCHEMA}.observations(
              collection_id,
              (body ->> 'content_hash'),
              (body ->> 'source_kind'),
              observation_id
            )
            """,
        ),
    ),
)


async def run_postgres_migrations(dsn: str) -> list[int]:
    """Apply ARGUS migrations under a PostgreSQL advisory lock."""

    applied: list[int] = []
    connection = await psycopg.AsyncConnection.connect(dsn, autocommit=True)
    lock_acquired = False
    try:
        await connection.execute("SELECT pg_advisory_lock(%s)", (_MIGRATION_LOCK_KEY,))
        lock_acquired = True
        for statement in _BOOTSTRAP_STATEMENTS:
            await connection.execute(statement)

        for migration in MIGRATIONS:
            cursor = await connection.execute(
                f"SELECT name, checksum FROM {_SCHEMA}.schema_migrations WHERE version=%s",
                (migration.version,),
            )
            row = await cursor.fetchone()
            if row is not None:
                existing_name, existing_checksum = str(row[0]), str(row[1])
                if existing_name != migration.name or existing_checksum != migration.checksum:
                    raise RuntimeError(
                        f"ARGUS migration {migration.version} checksum/name mismatch; "
                        "database schema cannot be trusted"
                    )
                continue

            async with connection.transaction():
                for statement in migration.statements:
                    await connection.execute(statement)
                await connection.execute(
                    f"""
                    INSERT INTO {_SCHEMA}.schema_migrations(version, name, checksum)
                    VALUES(%s, %s, %s)
                    """,
                    (migration.version, migration.name, migration.checksum),
                )
            applied.append(migration.version)
    finally:
        if lock_acquired:
            try:
                await connection.execute(
                    "SELECT pg_advisory_unlock(%s)",
                    (_MIGRATION_LOCK_KEY,),
                )
            except Exception:
                pass
        await connection.close()
    return applied


async def current_postgres_schema_version(dsn: str) -> int:
    connection = await psycopg.AsyncConnection.connect(dsn, autocommit=True)
    try:
        cursor = await connection.execute(
            "SELECT to_regclass('argus.schema_migrations') IS NOT NULL"
        )
        row = await cursor.fetchone()
        if not row or not bool(row[0]):
            return 0
        cursor = await connection.execute(
            "SELECT COALESCE(MAX(version), 0) FROM argus.schema_migrations"
        )
        row = await cursor.fetchone()
        return int(row[0]) if row else 0
    finally:
        await connection.close()


EXPECTED_SCHEMA_VERSION = MIGRATIONS[-1].version if MIGRATIONS else 0
