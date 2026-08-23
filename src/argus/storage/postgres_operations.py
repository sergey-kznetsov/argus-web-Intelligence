from __future__ import annotations

from datetime import datetime

import psycopg
from psycopg.rows import dict_row

from argus.contracts.models import CollectionStatus, CollectionSummary
from argus.pagination import CollectionCursor


class PostgresOperationsStore:
    """Low-volume administrative reads kept outside the collection Repository contract."""

    def __init__(self, dsn: str) -> None:
        value = dsn.strip()
        if not value:
            raise ValueError("PostgreSQL DSN must not be empty")
        self._dsn = value

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

        connection = await psycopg.AsyncConnection.connect(
            self._dsn,
            row_factory=dict_row,
        )
        try:
            cursor_result = await connection.execute(query, tuple(params))
            rows = await cursor_result.fetchall()
        finally:
            await connection.close()

        has_more = len(rows) > page_size
        selected = rows[:page_size]
        items = [self._summary(row) for row in selected]
        return items, has_more

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
