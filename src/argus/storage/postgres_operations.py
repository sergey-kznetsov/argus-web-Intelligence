from __future__ import annotations

from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from argus.contracts.models import CollectionRecord, CollectionStatus, CollectionSummary, Evidence, Observation
from argus.pagination import CollectionCursor
from argus.result_delivery import EvidenceSlice, ObservationSlice, ResultBundle, ResultStats, ResultTooLargeError


class PostgresOperationsStore:
    """Pooled read-side store for administrative and bounded result API reads."""

    def __init__(
        self,
        dsn: str,
        *,
        min_size: int = 0,
        max_size: int = 4,
        timeout_seconds: float = 30.0,
        max_waiting: int = 32,
    ) -> None:
        value = dsn.strip()
        if not value:
            raise ValueError("PostgreSQL DSN must not be empty")
        if max_waiting < 1:
            raise ValueError("PostgreSQL pool max_waiting must be positive")
        self._max_waiting = int(max_waiting)
        self._pool = AsyncConnectionPool(
            conninfo=value,
            min_size=min_size,
            max_size=max_size,
            timeout=timeout_seconds,
            max_waiting=self._max_waiting,
            open=False,
            kwargs={"row_factory": dict_row},
            name="argus-api-read",
        )
        self._opened = False

    async def initialize(self) -> None:
        if not self._opened:
            await self._pool.open(wait=True)
            self._opened = True

    async def close(self) -> None:
        if self._opened:
            await self._pool.close()
            self._opened = False

    def pool_stats(self) -> dict[str, int]:
        raw = self._pool.get_stats()
        keys = (
            "pool_min",
            "pool_max",
            "pool_size",
            "pool_available",
            "requests_waiting",
            "requests_num",
            "requests_queued",
            "requests_errors",
            "requests_wait_ms",
            "usage_ms",
        )
        payload = {key: int(raw.get(key, 0)) for key in keys}
        payload["max_waiting"] = self._max_waiting
        return payload

    async def list_collections(
        self,
        *,
        limit: int,
        status: CollectionStatus | None = None,
        consumer: str | None = None,
        cursor: CollectionCursor | None = None,
    ) -> tuple[list[CollectionSummary], bool]:
        page_size = max(1, min(int(limit), 100))
        clauses: list[str] = []
        params: list[object] = []
        if status is not None:
            clauses.append("c.status=%s")
            params.append(status.value)
        if consumer is not None:
            clauses.append("c.body #>> '{request,consumer}'=%s")
            params.append(consumer)
        if cursor is not None:
            clauses.append("(c.created_at, c.collection_id) < (%s, %s)")
            params.extend((cursor.created_at, cursor.collection_id))
        where_sql = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(page_size + 1)
        query = (
            """
            SELECT c.collection_id, c.status, c.created_at, c.updated_at,
              c.body #>> '{request,analysis_id}' AS analysis_id,
              c.body #>> '{request,consumer}' AS consumer,
              COALESCE((c.body->>'progress_percent')::INTEGER, 0) AS progress_percent,
              c.body->>'stage' AS stage,
              COALESCE((c.body->>'partial')::BOOLEAN, FALSE) AS partial,
              COALESCE(jsonb_array_length(COALESCE(c.body->'errors', '[]'::jsonb)), 0) AS error_count,
              (SELECT COUNT(*) FROM argus.observations o WHERE o.collection_id=c.collection_id) AS observation_count,
              (SELECT COUNT(*) FROM argus.evidence e WHERE e.collection_id=c.collection_id) AS evidence_count
            FROM argus.collections c
            """
            + where_sql
            + " ORDER BY c.created_at DESC, c.collection_id DESC LIMIT %s"
        )
        async with self._pool.connection() as conn:
            rows = await (await conn.execute(query, tuple(params))).fetchall()
        return [self._summary(row) for row in rows[:page_size]], len(rows) > page_size

    async def result_stats(self, collection_id: str) -> tuple[CollectionRecord, ResultStats] | None:
        await self._touch_result_access(collection_id)
        async with self._pool.connection() as conn:
            async with conn.transaction():
                await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                record = await self._record(conn, collection_id)
                return None if record is None else (record, await self._result_stats(conn, collection_id))

    async def read_bounded_result(
        self,
        collection_id: str,
        *,
        max_items: int,
        max_bytes: int,
    ) -> ResultBundle | None:
        await self._touch_result_access(collection_id)
        async with self._pool.connection() as conn:
            async with conn.transaction():
                await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                record = await self._record(conn, collection_id)
                if record is None:
                    return None
                stats = await self._result_stats(conn, collection_id)
                if stats.total_items > max(1, max_items) or stats.stored_bytes > max(1024, max_bytes):
                    raise ResultTooLargeError(stats)
                observation_rows = await (await conn.execute(
                    "SELECT body FROM argus.observations WHERE collection_id=%s ORDER BY observation_id ASC",
                    (collection_id,),
                )).fetchall()
                evidence_rows = await (await conn.execute(
                    "SELECT body FROM argus.evidence WHERE collection_id=%s ORDER BY evidence_id ASC",
                    (collection_id,),
                )).fetchall()
                return ResultBundle(
                    record=record,
                    stats=stats,
                    observations=[Observation.model_validate(row["body"]) for row in observation_rows],
                    evidence=[Evidence.model_validate(row["body"]) for row in evidence_rows],
                )

    async def observation_page(
        self,
        collection_id: str,
        *,
        after_id: str | None,
        limit: int,
        max_bytes: int,
    ) -> ObservationSlice | None:
        await self._touch_result_access(collection_id)
        page_size = max(1, min(int(limit), 500))
        byte_limit = max(1024, int(max_bytes))
        async with self._pool.connection() as conn:
            async with conn.transaction():
                await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                record = await self._record(conn, collection_id)
                if record is None:
                    return None
                total = await self._count(conn, "observations", collection_id)
                rows = await self._bounded_rows(
                    conn,
                    table="observations",
                    id_column="observation_id",
                    collection_id=collection_id,
                    after_id=after_id,
                    limit=page_size,
                    max_bytes=byte_limit,
                )
                items = [Observation.model_validate(row["body"]) for row in rows]
                used_bytes = sum(int(row["body_bytes"] or 0) for row in rows)
                has_more = bool(items) and await self._has_after(
                    conn, "observations", "observation_id", collection_id, items[-1].observation_id
                )
                return ObservationSlice(record, total, used_bytes, items, has_more)

    async def evidence_page(
        self,
        collection_id: str,
        *,
        after_id: str | None,
        limit: int,
        max_bytes: int,
    ) -> EvidenceSlice | None:
        await self._touch_result_access(collection_id)
        page_size = max(1, min(int(limit), 500))
        byte_limit = max(1024, int(max_bytes))
        async with self._pool.connection() as conn:
            async with conn.transaction():
                await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                record = await self._record(conn, collection_id)
                if record is None:
                    return None
                total = await self._count(conn, "evidence", collection_id)
                rows = await self._bounded_rows(
                    conn,
                    table="evidence",
                    id_column="evidence_id",
                    collection_id=collection_id,
                    after_id=after_id,
                    limit=page_size,
                    max_bytes=byte_limit,
                )
                items = [Evidence.model_validate(row["body"]) for row in rows]
                used_bytes = sum(int(row["body_bytes"] or 0) for row in rows)
                has_more = bool(items) and await self._has_after(
                    conn, "evidence", "evidence_id", collection_id, items[-1].evidence_id
                )
                return EvidenceSlice(record, total, used_bytes, items, has_more)

    async def _touch_result_access(self, collection_id: str) -> bool:
        async with self._pool.connection() as conn:
            cursor = await conn.execute(
                """
                INSERT INTO argus.collection_result_access(collection_id, last_accessed_at)
                SELECT collection_id, NOW()
                FROM argus.collections
                WHERE collection_id=%s
                ON CONFLICT (collection_id) DO UPDATE
                SET last_accessed_at=EXCLUDED.last_accessed_at
                """,
                (collection_id,),
            )
        return cursor.rowcount == 1

    @staticmethod
    async def _bounded_rows(
        conn: Any,
        *,
        table: str,
        id_column: str,
        collection_id: str,
        after_id: str | None,
        limit: int,
        max_bytes: int,
    ) -> list[dict[str, object]]:
        if (table, id_column) not in {("observations", "observation_id"), ("evidence", "evidence_id")}:
            raise ValueError("invalid result table")
        params: list[object] = [collection_id]
        after_sql = ""
        if after_id is not None:
            after_sql = f" AND {id_column}>%s"
            params.append(after_id)
        params.extend((limit, max_bytes))
        query = f"""
            WITH base AS (
              SELECT {id_column}, body, OCTET_LENGTH(body::text) AS body_bytes
              FROM argus.{table}
              WHERE collection_id=%s {after_sql}
              ORDER BY {id_column} ASC
              LIMIT %s
            ), ranked AS (
              SELECT *, ROW_NUMBER() OVER (ORDER BY {id_column}) AS rn,
                SUM(body_bytes) OVER (ORDER BY {id_column}) AS cumulative_bytes
              FROM base
            )
            SELECT {id_column}, body, body_bytes
            FROM ranked
            WHERE rn=1 OR cumulative_bytes<=%s
            ORDER BY {id_column} ASC
        """
        return await (await conn.execute(query, tuple(params))).fetchall()

    @staticmethod
    async def _has_after(conn: Any, table: str, id_column: str, collection_id: str, item_id: str) -> bool:
        if (table, id_column) not in {("observations", "observation_id"), ("evidence", "evidence_id")}:
            raise ValueError("invalid result table")
        row = await (await conn.execute(
            f"SELECT 1 FROM argus.{table} WHERE collection_id=%s AND {id_column}>%s LIMIT 1",
            (collection_id, item_id),
        )).fetchone()
        return row is not None

    @staticmethod
    async def _record(conn: Any, collection_id: str) -> CollectionRecord | None:
        row = await (await conn.execute(
            "SELECT body FROM argus.collections WHERE collection_id=%s", (collection_id,)
        )).fetchone()
        return CollectionRecord.model_validate(row["body"]) if row else None

    @staticmethod
    async def _result_stats(conn: Any, collection_id: str) -> ResultStats:
        row = await (await conn.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM argus.observations WHERE collection_id=%s) AS observations,
              (SELECT COUNT(*) FROM argus.evidence WHERE collection_id=%s) AS evidence,
              COALESCE((SELECT SUM(OCTET_LENGTH(body::text)) FROM argus.observations WHERE collection_id=%s), 0)
              + COALESCE((SELECT SUM(OCTET_LENGTH(body::text)) FROM argus.evidence WHERE collection_id=%s), 0)
              AS stored_bytes
            """,
            (collection_id, collection_id, collection_id, collection_id),
        )).fetchone()
        return ResultStats(
            observation_count=int(row["observations"] or 0),
            evidence_count=int(row["evidence"] or 0),
            stored_bytes=int(row["stored_bytes"] or 0),
        )

    @staticmethod
    async def _count(conn: Any, table: str, collection_id: str) -> int:
        if table not in {"observations", "evidence"}:
            raise ValueError("invalid result table")
        row = await (await conn.execute(
            f"SELECT COUNT(*) AS count FROM argus.{table} WHERE collection_id=%s", (collection_id,)
        )).fetchone()
        return int(row["count"] or 0)

    @staticmethod
    def _summary(row: dict[str, object]) -> CollectionSummary:
        return CollectionSummary(
            collection_id=str(row["collection_id"]),
            analysis_id=str(row["analysis_id"] or ""),
            consumer=str(row["consumer"] or ""),
            status=CollectionStatus(str(row["status"])),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            progress_percent=int(row["progress_percent"] or 0),
            stage=str(row["stage"]) if row["stage"] is not None else None,
            partial=bool(row["partial"]),
            error_count=int(row["error_count"] or 0),
            observation_count=int(row["observation_count"] or 0),
            evidence_count=int(row["evidence_count"] or 0),
        )
