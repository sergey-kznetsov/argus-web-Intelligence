from __future__ import annotations

import asyncio
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from argus.contracts.models import CollectionRecord, Evidence, Observation, Snapshot
from argus.recipes.models import SiteRecipe
from argus.storage.postgres_migrations import EXPECTED_SCHEMA_VERSION


class PostgresRepository:
    def __init__(
        self,
        dsn: str,
        *,
        min_size: int = 1,
        max_size: int = 8,
        timeout_seconds: float = 30.0,
    ) -> None:
        if not dsn.strip():
            raise ValueError("PostgreSQL DSN must not be empty")
        if min_size < 0 or max_size < 1 or min_size > max_size:
            raise ValueError("invalid PostgreSQL pool size")
        self._pool = AsyncConnectionPool(
            conninfo=dsn,
            min_size=min_size,
            max_size=max_size,
            timeout=timeout_seconds,
            open=False,
            kwargs={"row_factory": dict_row},
            name="argus",
        )
        self._open_lock = asyncio.Lock()
        self._opened = False

    async def initialize(self) -> None:
        async with self._open_lock:
            if not self._opened:
                await self._pool.open(wait=True)
                self._opened = True
        await self._verify_schema()

    async def close(self) -> None:
        async with self._open_lock:
            if self._opened:
                await self._pool.close()
                self._opened = False

    async def health(self) -> dict[str, object]:
        try:
            async with self._pool.connection() as conn:
                cursor = await conn.execute(
                    "SELECT COALESCE(MAX(version), 0) AS version "
                    "FROM argus.schema_migrations"
                )
                row = await cursor.fetchone()
            version = int(row["version"]) if row else 0
            return {
                "status": "ok" if version == EXPECTED_SCHEMA_VERSION else "degraded",
                "backend": "postgresql",
                "schema_version": version,
                "expected_schema_version": EXPECTED_SCHEMA_VERSION,
            }
        except Exception:
            return {
                "status": "error",
                "backend": "postgresql",
                "schema_version": None,
                "expected_schema_version": EXPECTED_SCHEMA_VERSION,
            }

    async def _verify_schema(self) -> None:
        async with self._pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT to_regclass('argus.schema_migrations') AS migrations, "
                "to_regclass('argus.collections') AS collections, "
                "to_regclass('argus.collection_leases') AS collection_leases, "
                "to_regclass('argus.worker_instances') AS worker_instances"
            )
            row = await cursor.fetchone()
            if (
                not row
                or row["migrations"] is None
                or row["collections"] is None
                or row["collection_leases"] is None
                or row["worker_instances"] is None
            ):
                raise RuntimeError(
                    "ARGUS PostgreSQL schema is not migrated; "
                    "run `python -m argus.storage.cli migrate`"
                )
            cursor = await conn.execute(
                "SELECT COALESCE(MAX(version), 0) AS version "
                "FROM argus.schema_migrations"
            )
            row = await cursor.fetchone()
            version = int(row["version"]) if row else 0
            if version != EXPECTED_SCHEMA_VERSION:
                raise RuntimeError(
                    f"ARGUS PostgreSQL schema version {version} does not match "
                    f"expected {EXPECTED_SCHEMA_VERSION}; run migrations"
                )

    async def create_collection(self, record: CollectionRecord) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO argus.collections(collection_id, status, body, created_at, updated_at)
                VALUES(%s, %s, %s, %s, %s)
                """,
                (
                    record.collection_id,
                    record.status.value,
                    Jsonb(record.model_dump(mode="json")),
                    record.created_at,
                    record.updated_at,
                ),
            )

    async def update_collection(self, record: CollectionRecord) -> None:
        """Persist collection state without allowing stale workers to resurrect cancellation."""

        async with self._pool.connection() as conn:
            await conn.execute(
                """
                UPDATE argus.collections
                SET status=%s, body=%s, updated_at=%s
                WHERE collection_id=%s
                  AND (status <> 'cancelled' OR %s = 'cancelled')
                """,
                (
                    record.status.value,
                    Jsonb(record.model_dump(mode="json")),
                    record.updated_at,
                    record.collection_id,
                    record.status.value,
                ),
            )

    async def get_collection(self, collection_id: str) -> CollectionRecord | None:
        async with self._pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT body FROM argus.collections WHERE collection_id=%s",
                (collection_id,),
            )
            row = await cursor.fetchone()
        return CollectionRecord.model_validate(row["body"]) if row else None

    async def list_recoverable_collections(self) -> list[CollectionRecord]:
        async with self._pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT body FROM argus.collections
                WHERE status IN ('queued', 'running')
                ORDER BY created_at ASC
                """
            )
            rows = await cursor.fetchall()
        return [CollectionRecord.model_validate(row["body"]) for row in rows]

    async def register_worker(
        self,
        worker_id: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        worker = worker_id.strip()
        if not worker:
            raise ValueError("worker_id must not be empty")
        async with self._pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO argus.worker_instances(worker_id, started_at, heartbeat_at, metadata)
                VALUES(%s, NOW(), NOW(), %s)
                ON CONFLICT (worker_id) DO UPDATE
                SET started_at=NOW(), heartbeat_at=NOW(), metadata=EXCLUDED.metadata
                """,
                (worker, Jsonb(metadata or {})),
            )

    async def heartbeat_worker(self, worker_id: str) -> bool:
        async with self._pool.connection() as conn:
            cursor = await conn.execute(
                """
                UPDATE argus.worker_instances
                SET heartbeat_at=NOW()
                WHERE worker_id=%s
                """,
                (worker_id,),
            )
        return cursor.rowcount == 1

    async def unregister_worker(self, worker_id: str) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "DELETE FROM argus.worker_instances WHERE worker_id=%s",
                (worker_id,),
            )

    async def active_worker_count(self, *, max_age_seconds: float) -> int:
        age = max(1.0, float(max_age_seconds))
        async with self._pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM argus.worker_instances
                WHERE heartbeat_at >= NOW() - (%s * INTERVAL '1 second')
                """,
                (age,),
            )
            row = await cursor.fetchone()
        return int(row["count"]) if row else 0

    async def claim_next_collection(
        self,
        worker_id: str,
        *,
        lease_seconds: float,
    ) -> str | None:
        lease = max(1.0, float(lease_seconds))
        async with self._pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT c.collection_id
                FROM argus.collections AS c
                LEFT JOIN argus.collection_leases AS l
                  ON l.collection_id = c.collection_id
                WHERE c.status IN ('queued', 'running')
                  AND (l.collection_id IS NULL OR l.lease_until <= NOW())
                ORDER BY
                  CASE c.status WHEN 'queued' THEN 0 ELSE 1 END,
                  c.created_at ASC,
                  c.collection_id ASC
                FOR UPDATE OF c SKIP LOCKED
                LIMIT 1
                """
            )
            row = await cursor.fetchone()
            if not row:
                return None
            collection_id = str(row["collection_id"])
            cursor = await conn.execute(
                """
                INSERT INTO argus.collection_leases(
                  collection_id, worker_id, leased_at, heartbeat_at, lease_until
                )
                VALUES(%s, %s, NOW(), NOW(), NOW() + (%s * INTERVAL '1 second'))
                ON CONFLICT (collection_id) DO UPDATE
                SET worker_id=EXCLUDED.worker_id,
                    leased_at=NOW(),
                    heartbeat_at=NOW(),
                    lease_until=EXCLUDED.lease_until
                WHERE argus.collection_leases.lease_until <= NOW()
                   OR argus.collection_leases.worker_id = EXCLUDED.worker_id
                """,
                (collection_id, worker_id, lease),
            )
            if cursor.rowcount != 1:
                return None
            return collection_id

    async def renew_collection_lease(
        self,
        collection_id: str,
        worker_id: str,
        *,
        lease_seconds: float,
    ) -> bool:
        lease = max(1.0, float(lease_seconds))
        async with self._pool.connection() as conn:
            cursor = await conn.execute(
                """
                UPDATE argus.collection_leases
                SET heartbeat_at=NOW(),
                    lease_until=NOW() + (%s * INTERVAL '1 second')
                WHERE collection_id=%s
                  AND worker_id=%s
                  AND lease_until > NOW()
                """,
                (lease, collection_id, worker_id),
            )
        return cursor.rowcount == 1

    async def release_collection_lease(
        self,
        collection_id: str,
        worker_id: str,
    ) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                """
                DELETE FROM argus.collection_leases
                WHERE collection_id=%s AND worker_id=%s
                """,
                (collection_id, worker_id),
            )

    async def add_observation(self, observation: Observation) -> None:
        await self._upsert_collection_json(
            table="observations",
            id_column="observation_id",
            item_id=observation.observation_id,
            collection_id=observation.collection_id,
            body=observation.model_dump(mode="json"),
        )

    async def list_observations(self, collection_id: str) -> list[Observation]:
        rows = await self._list_collection_json("observations", collection_id)
        return [Observation.model_validate(row) for row in rows]

    async def add_evidence(self, evidence: Evidence, collection_id: str) -> None:
        await self._upsert_collection_json(
            table="evidence",
            id_column="evidence_id",
            item_id=evidence.evidence_id,
            collection_id=collection_id,
            body=evidence.model_dump(mode="json"),
        )

    async def list_evidence(self, collection_id: str) -> list[Evidence]:
        rows = await self._list_collection_json("evidence", collection_id)
        return [Evidence.model_validate(row) for row in rows]

    async def _upsert_collection_json(
        self,
        *,
        table: str,
        id_column: str,
        item_id: str,
        collection_id: str,
        body: dict[str, Any],
    ) -> None:
        if table not in {"observations", "evidence"} or id_column not in {
            "observation_id",
            "evidence_id",
        }:
            raise ValueError("invalid table")
        async with self._pool.connection() as conn:
            await conn.execute(
                f"""
                INSERT INTO argus.{table}({id_column}, collection_id, body)
                SELECT %s, %s, %s
                WHERE EXISTS (
                  SELECT 1 FROM argus.collections
                  WHERE collection_id=%s AND status <> 'cancelled'
                )
                ON CONFLICT ({id_column}) DO UPDATE
                SET collection_id=EXCLUDED.collection_id, body=EXCLUDED.body
                WHERE EXISTS (
                  SELECT 1 FROM argus.collections
                  WHERE collection_id=%s AND status <> 'cancelled'
                )
                """,
                (item_id, collection_id, Jsonb(body), collection_id, collection_id),
            )

    async def _list_collection_json(
        self,
        table: str,
        collection_id: str,
    ) -> list[dict[str, Any]]:
        id_columns = {
            "observations": "observation_id",
            "evidence": "evidence_id",
        }
        id_column = id_columns.get(table)
        if id_column is None:
            raise ValueError("invalid table")
        async with self._pool.connection() as conn:
            cursor = await conn.execute(
                f"""
                SELECT body FROM argus.{table}
                WHERE collection_id=%s
                ORDER BY {id_column}
                """,
                (collection_id,),
            )
            rows = await cursor.fetchall()
        return [row["body"] for row in rows]

    async def add_snapshot(self, snapshot: Snapshot) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO argus.snapshots(snapshot_id, source_url, collected_at, body)
                VALUES(%s, %s, %s, %s)
                ON CONFLICT (snapshot_id) DO NOTHING
                """,
                (
                    snapshot.snapshot_id,
                    snapshot.source_url,
                    snapshot.collected_at,
                    Jsonb(snapshot.model_dump(mode="json")),
                ),
            )

    async def latest_snapshot(self, source_url: str) -> Snapshot | None:
        async with self._pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT body FROM argus.snapshots
                WHERE source_url=%s
                ORDER BY collected_at DESC
                LIMIT 1
                """,
                (source_url,),
            )
            row = await cursor.fetchone()
        return Snapshot.model_validate(row["body"]) if row else None

    async def save_recipe(self, recipe: SiteRecipe) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO argus.site_recipes(recipe_id, domain, goal, version, body)
                VALUES(%s, %s, %s, %s, %s)
                ON CONFLICT (domain, goal, version) DO UPDATE
                SET recipe_id=EXCLUDED.recipe_id, body=EXCLUDED.body
                """,
                (
                    recipe.recipe_id,
                    recipe.domain,
                    recipe.goal,
                    recipe.version,
                    Jsonb(recipe.model_dump(mode="json")),
                ),
            )

    async def get_recipe(self, domain: str, goal: str) -> SiteRecipe | None:
        async with self._pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT body FROM argus.site_recipes
                WHERE domain=%s AND goal=%s
                ORDER BY version DESC
                LIMIT 1
                """,
                (domain, goal),
            )
            row = await cursor.fetchone()
        return SiteRecipe.model_validate(row["body"]) if row else None
