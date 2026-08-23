from __future__ import annotations

from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from argus.contracts.models import (
    CollectionRecord,
    CollectionStatus,
    CollectionSummary,
    Evidence,
    Observation,
)
from argus.pagination import CollectionCursor
from argus.result_delivery import (
    EvidenceSlice,
    ObservationSlice,
    ResultBundle,
    ResultStats,
    ResultTooLargeError,
)


class PostgresOperationsStore:
    """Pooled read-side store for administrative and bounded result API reads."""

    def __init__(
        self,
        dsn: str,
        *,
        min_size: int = 0,
        max_size: int = 4,
        timeout_seconds: float = 30.0,
    ) -> None:
        value = dsn.strip()
        if not value:
            raise ValueError("PostgreSQL DSN must not be empty")
        self._pool = AsyncConnectionPool(
            conninfo=value,
            min_size=min_size,
            max_size=max_size,
            timeout=timeout_seconds,
            open=False,
            kwargs={"row_factory": dict_row},
            name="argus-api-read",
        )
        self._opened = False

    async def initialize(self) -> None:
        if self._opened:
            return
        await self._pool.open(wait=True)
        self._opened = True

    async def close(self) -> None:
        if not self._opened:
            return
        await self._pool.close()
        self._opened = False

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
            SELECT
              c.collection_id,
              c.status,
              c.created_at,
              c.updated_at,
              c.body #>> '{request,analysis_id}' AS analysis_id,
              c.body #>> '{request,consumer}' AS consumer,
              COALESCE((c.body->>'progress_percent')::INTEGER, 0) AS progress_percent,
              c.body->>'stage' AS stage,
              COALESCE((c.body->>'partial')::BOOLEAN, FALSE) AS partial,
              COALESCE(jsonb_array_length(COALESCE(c.body->'errors', '[]'::jsonb)), 0)
                AS error_count,
              (SELECT COUNT(*) FROM argus.observations o
                WHERE o.collection_id=c.collection_id) AS observation_count,
              (SELECT COUNT(*) FROM argus.evidence e
                WHERE e.collection_id=c.collection_id) AS evidence_count
            FROM argus.collections c
            """
            + where_sql
            + """
            ORDER BY c.created_at DESC, c.collection_id DESC
            LIMIT %s
            """
        )

        async with self._pool.connection() as conn:
            cursor_result = await conn.execute(query, tuple(params))
            rows = await cursor_result.fetchall()

        has_more = len(rows) > page_size
        selected = rows[:page_size]
        items = [self._summary(row) for row in selected]
        return items, has_more

    async def result_stats(
        self,
        collection_id: str,
    ) -> tuple[CollectionRecord, ResultStats] | None:
        async with self._pool.connection() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
                )
                record = await self._record(conn, collection_id)
                if record is None:
                    return None
                return record, await self._result_stats(conn, collection_id)

    async def read_bounded_result(
        self,
        collection_id: str,
        *,
        max_items: int,
        max_bytes: int,
    ) -> ResultBundle | None:
        async with self._pool.connection() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
                )
                record = await self._record(conn, collection_id)
                if record is None:
                    return None
                stats = await self._result_stats(conn, collection_id)
                if stats.total_items > max(1, max_items) or stats.stored_bytes > max(1024, max_bytes):
                    raise ResultTooLargeError(stats)
                observation_rows = await (
                    await conn.execute(
                        """
                        SELECT body FROM argus.observations
                        WHERE collection_id=%s
                        ORDER BY observation_id ASC
                        """,
                        (collection_id,),
                    )
                ).fetchall()
                evidence_rows = await (
                    await conn.execute(
                        """
                        SELECT body FROM argus.evidence
                        WHERE collection_id=%s
                        ORDER BY evidence_id ASC
                        """,
                        (collection_id,),
                    )
                ).fetchall()
                return ResultBundle(
                    record=record,
                    stats=stats,
                    observations=[
                        Observation.model_validate(row["body"]) for row in observation_rows
                    ],
                    evidence=[Evidence.model_validate(row["body"]) for row in evidence_rows],
                )

    async def observation_page(
        self,
        collection_id: str,
        *,
        after_id: str | None,
        limit: int,
    ) -> ObservationSlice | None:
        page_size = max(1, min(int(limit), 500))
        async with self._pool.connection() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
                )
                record = await self._record(conn, collection_id)
                if record is None:
                    return None
                total = await self._count(conn, "observations", collection_id)
                params: list[object] = [collection_id]
                after_sql = ""
                if after_id is not None:
                    after_sql = " AND observation_id>%s"
                    params.append(after_id)
                params.append(page_size + 1)
                rows = await (
                    await conn.execute(
                        """
                        SELECT body FROM argus.observations
                        WHERE collection_id=%s
                        """
                        + after_sql
                        + " ORDER BY observation_id ASC LIMIT %s",
                        tuple(params),
                    )
                ).fetchall()
                return ObservationSlice(
                    record=record,
                    total_count=total,
                    items=[
                        Observation.model_validate(row["body"])
                        for row in rows[:page_size]
                    ],
                    has_more=len(rows) > page_size,
                )

    async def evidence_page(
        self,
        collection_id: str,
        *,
        after_id: str | None,
        limit: int,
    ) -> EvidenceSlice | None:
        page_size = max(1, min(int(limit), 500))
        async with self._pool.connection() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
                )
                record = await self._record(conn, collection_id)
                if record is None:
                    return None
                total = await self._count(conn, "evidence", collection_id)
                params: list[object] = [collection_id]
                after_sql = ""
                if after_id is not None:
                    after_sql = " AND evidence_id>%s"
                    params.append(after_id)
                params.append(page_size + 1)
                rows = await (
                    await conn.execute(
                        """
                        SELECT body FROM argus.evidence
                        WHERE collection_id=%s
                        """
                        + after_sql
                        + " ORDER BY evidence_id ASC LIMIT %s",
                        tuple(params),
                    )
                ).fetchall()
                return EvidenceSlice(
                    record=record,
                    total_count=total,
                    items=[Evidence.model_validate(row["body"]) for row in rows[:page_size]],
                    has_more=len(rows) > page_size,
                )

    @staticmethod
    async def _record(conn: Any, collection_id: str) -> CollectionRecord | None:
        row = await (
            await conn.execute(
                "SELECT body FROM argus.collections WHERE collection_id=%s",
                (collection_id,),
            )
        ).fetchone()
        return CollectionRecord.model_validate(row["body"]) if row else None

    @staticmethod
    async def _result_stats(conn: Any, collection_id: str) -> ResultStats:
        row = await (
            await conn.execute(
                """
                SELECT
                  (SELECT COUNT(*) FROM argus.observations WHERE collection_id=%s)
                    AS observations,
                  (SELECT COUNT(*) FROM argus.evidence WHERE collection_id=%s)
                    AS evidence,
                  COALESCE((
                    SELECT SUM(OCTET_LENGTH(body::text))
                    FROM argus.observations WHERE collection_id=%s
                  ), 0) + COALESCE((
                    SELECT SUM(OCTET_LENGTH(body::text))
                    FROM argus.evidence WHERE collection_id=%s
                  ), 0) AS stored_bytes
                """,
                (collection_id, collection_id, collection_id, collection_id),
            )
        ).fetchone()
        return ResultStats(
            observation_count=int(row["observations"] or 0),
            evidence_count=int(row["evidence"] or 0),
            stored_bytes=int(row["stored_bytes"] or 0),
        )

    @staticmethod
    async def _count(conn: Any, table: str, collection_id: str) -> int:
        if table not in {"observations", "evidence"}:
            raise ValueError("invalid result table")
        row = await (
            await conn.execute(
                f"SELECT COUNT(*) AS count FROM argus.{table} WHERE collection_id=%s",
                (collection_id,),
            )
        ).fetchone()
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
