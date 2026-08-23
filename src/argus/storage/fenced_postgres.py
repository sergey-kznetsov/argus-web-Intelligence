from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from argus.contracts.models import CollectionRecord, Snapshot
from argus.storage.lease_fencing import LeaseLostError, current_lease_fence
from argus.storage.postgres import PostgresRepository


class FencedPostgresRepository(PostgresRepository):
    """PostgreSQL repository that rejects stale worker mutations after lease transfer.

    API/admin calls have no lease context and retain the normal repository behavior.
    Worker execution installs a per-task lease context; collection-scoped mutations are
    then accepted only while that worker still owns a non-expired lease.
    """

    async def update_collection(self, record: CollectionRecord) -> None:
        fence = current_lease_fence(record.collection_id)
        if fence is None:
            await super().update_collection(record)
            return

        async with self._pool.connection() as conn:
            cursor = await conn.execute(
                """
                UPDATE argus.collections
                SET status=%s, body=%s, updated_at=%s
                WHERE collection_id=%s
                  AND (status <> 'cancelled' OR %s = 'cancelled')
                  AND EXISTS (
                    SELECT 1
                    FROM argus.collection_leases AS l
                    WHERE l.collection_id=%s
                      AND l.worker_id=%s
                      AND l.lease_until > NOW()
                  )
                """,
                (
                    record.status.value,
                    Jsonb(record.model_dump(mode="json")),
                    record.updated_at,
                    record.collection_id,
                    record.status.value,
                    record.collection_id,
                    fence.worker_id,
                ),
            )
        if cursor.rowcount != 1:
            raise LeaseLostError(
                f"worker {fence.worker_id} no longer owns lease for {record.collection_id}"
            )

    async def _upsert_collection_json(
        self,
        *,
        table: str,
        id_column: str,
        item_id: str,
        collection_id: str,
        body: dict[str, Any],
    ) -> None:
        fence = current_lease_fence(collection_id)
        if fence is None:
            await super()._upsert_collection_json(
                table=table,
                id_column=id_column,
                item_id=item_id,
                collection_id=collection_id,
                body=body,
            )
            return
        if table not in {"observations", "evidence"} or id_column not in {
            "observation_id",
            "evidence_id",
        }:
            raise ValueError("invalid table")

        async with self._pool.connection() as conn:
            cursor = await conn.execute(
                f"""
                INSERT INTO argus.{table}({id_column}, collection_id, body)
                SELECT %s, %s, %s
                WHERE EXISTS (
                  SELECT 1 FROM argus.collections
                  WHERE collection_id=%s AND status <> 'cancelled'
                )
                  AND EXISTS (
                    SELECT 1 FROM argus.collection_leases AS l
                    WHERE l.collection_id=%s
                      AND l.worker_id=%s
                      AND l.lease_until > NOW()
                  )
                ON CONFLICT ({id_column}) DO UPDATE
                SET collection_id=EXCLUDED.collection_id, body=EXCLUDED.body
                WHERE EXISTS (
                  SELECT 1 FROM argus.collections
                  WHERE collection_id=%s AND status <> 'cancelled'
                )
                  AND EXISTS (
                    SELECT 1 FROM argus.collection_leases AS l
                    WHERE l.collection_id=%s
                      AND l.worker_id=%s
                      AND l.lease_until > NOW()
                  )
                """,
                (
                    item_id,
                    collection_id,
                    Jsonb(body),
                    collection_id,
                    collection_id,
                    fence.worker_id,
                    collection_id,
                    collection_id,
                    fence.worker_id,
                ),
            )
        if cursor.rowcount != 1:
            raise LeaseLostError(
                f"worker {fence.worker_id} no longer owns lease for {collection_id}"
            )

    async def add_snapshot(
        self,
        snapshot: Snapshot,
        collection_id: str | None = None,
    ) -> None:
        normalized_collection_id = (collection_id or "").strip()
        fence = (
            current_lease_fence(normalized_collection_id)
            if normalized_collection_id
            else None
        )
        if fence is None:
            await super().add_snapshot(snapshot)
            return

        async with self._pool.connection() as conn:
            cursor = await conn.execute(
                """
                INSERT INTO argus.snapshots(snapshot_id, source_url, collected_at, body)
                SELECT %s, %s, %s, %s
                WHERE EXISTS (
                  SELECT 1 FROM argus.collection_leases AS l
                  WHERE l.collection_id=%s
                    AND l.worker_id=%s
                    AND l.lease_until > NOW()
                )
                ON CONFLICT (snapshot_id) DO NOTHING
                """,
                (
                    snapshot.snapshot_id,
                    snapshot.source_url,
                    snapshot.collected_at,
                    Jsonb(snapshot.model_dump(mode="json")),
                    normalized_collection_id,
                    fence.worker_id,
                ),
            )
            if cursor.rowcount == 1:
                return
            existing = await (
                await conn.execute(
                    "SELECT 1 FROM argus.snapshots WHERE snapshot_id=%s",
                    (snapshot.snapshot_id,),
                )
            ).fetchone()
        if existing is not None:
            return
        raise LeaseLostError(
            f"worker {fence.worker_id} no longer owns lease for {normalized_collection_id}"
        )
