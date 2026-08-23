from __future__ import annotations

from typing import Any

import psycopg
from psycopg.rows import dict_row

_JSONB_TABLES = (
    "collections",
    "observations",
    "evidence",
    "snapshots",
    "site_recipes",
)
_RELATION_TABLES = (
    "collections",
    "observations",
    "evidence",
    "snapshots",
    "site_recipes",
    "collection_leases",
    "worker_instances",
    "collection_idempotency",
    "collection_result_access",
    "schema_migrations",
)


async def postgres_storage_stats(dsn: str) -> dict[str, object]:
    """Return bounded ARGUS schema size metrics without loading JSONB values into Python."""

    if not dsn.strip():
        raise ValueError("PostgreSQL DSN must not be empty")
    connection = await psycopg.AsyncConnection.connect(
        dsn,
        autocommit=True,
        row_factory=dict_row,
    )
    try:
        jsonb: dict[str, dict[str, int]] = {}
        for table in _JSONB_TABLES:
            row = await (
                await connection.execute(
                    f"""
                    SELECT
                      COUNT(*)::BIGINT AS rows,
                      COALESCE(SUM(pg_column_size(body)), 0)::BIGINT AS body_bytes,
                      COALESCE(MAX(pg_column_size(body)), 0)::BIGINT AS max_body_bytes,
                      COALESCE(AVG(pg_column_size(body)), 0)::BIGINT AS avg_body_bytes
                    FROM argus.{table}
                    """
                )
            ).fetchone()
            jsonb[table] = {
                "rows": int(row["rows"] or 0),
                "body_bytes": int(row["body_bytes"] or 0),
                "max_body_bytes": int(row["max_body_bytes"] or 0),
                "avg_body_bytes": int(row["avg_body_bytes"] or 0),
            }

        relations: dict[str, dict[str, int]] = {}
        total_relation_bytes = 0
        for table in _RELATION_TABLES:
            relation = f"argus.{table}"
            row = await (
                await connection.execute(
                    """
                    SELECT
                      pg_table_size(%s::regclass)::BIGINT AS table_bytes,
                      pg_indexes_size(%s::regclass)::BIGINT AS index_bytes,
                      pg_total_relation_size(%s::regclass)::BIGINT AS total_bytes
                    """,
                    (relation, relation, relation),
                )
            ).fetchone()
            table_bytes = int(row["table_bytes"] or 0)
            index_bytes = int(row["index_bytes"] or 0)
            total_bytes = int(row["total_bytes"] or 0)
            total_relation_bytes += total_bytes
            relations[table] = {
                "table_bytes": table_bytes,
                "index_bytes": index_bytes,
                "total_bytes": total_bytes,
            }

        largest_jsonb_table = max(
            _JSONB_TABLES,
            key=lambda table: jsonb[table]["body_bytes"],
        )
        largest_jsonb_row_table = max(
            _JSONB_TABLES,
            key=lambda table: jsonb[table]["max_body_bytes"],
        )
        return {
            "schema": "argus",
            "jsonb": jsonb,
            "relations": relations,
            "total_relation_bytes": total_relation_bytes,
            "largest_jsonb_table": largest_jsonb_table,
            "largest_jsonb_table_bytes": jsonb[largest_jsonb_table]["body_bytes"],
            "largest_jsonb_row_table": largest_jsonb_row_table,
            "largest_jsonb_row_bytes": jsonb[largest_jsonb_row_table]["max_body_bytes"],
        }
    finally:
        await connection.close()
