from __future__ import annotations

import logging
from typing import Any, NoReturn

from psycopg.types.json import Jsonb

from argus.contracts.models import CollectionRecord, Evidence, Observation, Snapshot
from argus.recipes.models import SiteRecipe
from argus.storage.lease_fencing import (
    LeaseFence,
    LeaseLostError,
    WorkerStorageError,
    active_lease_fence,
    current_lease_fence,
)
from argus.storage.postgres import PostgresRepository

logger = logging.getLogger("argus.storage.fenced_postgres")


class FencedPostgresRepository(PostgresRepository):
    """PostgreSQL repository that rejects stale worker mutations after lease transfer.

    API/admin calls have no lease context and retain the normal repository behavior.
    Worker execution installs a per-task lease context; collection-scoped mutations are
    accepted only while that worker still owns a non-expired lease. Storage failures
    inside that context abort the worker attempt so the persisted checkpoint can replay.
    """

    @staticmethod
    def _abort_storage_attempt(
        fence: LeaseFence,
        operation: str,
        exc: Exception,
    ) -> NoReturn:
        logger.error(
            "lease-owned storage operation failed",
            extra={
                "event": "worker_storage_failed",
                "worker_id": fence.worker_id,
                "collection_id": fence.collection_id,
                "operation": operation,
                "error_type": type(exc).__name__,
            },
        )
        raise WorkerStorageError(
            f"storage operation '{operation}' failed for collection {fence.collection_id}"
        ) from exc

    async def get_collection(self, collection_id: str) -> CollectionRecord | None:
        fence = current_lease_fence(collection_id)
        if fence is None:
            return await super().get_collection(collection_id)
        try:
            return await super().get_collection(collection_id)
        except Exception as exc:
            self._abort_storage_attempt(fence, "get_collection", exc)

    async def update_collection(self, record: CollectionRecord) -> None:
        fence = current_lease_fence(record.collection_id)
        if fence is None:
            await super().update_collection(record)
            return

        try:
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
        except Exception as exc:
            self._abort_storage_attempt(fence, "update_collection", exc)
        if cursor.rowcount != 1:
            raise LeaseLostError(
                f"worker {fence.worker_id} no longer owns lease for {record.collection_id}"
            )

    async def commit_task_success(
        self,
        record: CollectionRecord,
        *,
        observations: list[Observation],
        evidence: list[Evidence],
        snapshots: list[Snapshot],
    ) -> None:
        """Atomically persist one successful source task and its resulting checkpoint."""

        if any(item.collection_id != record.collection_id for item in observations):
            raise ValueError("task observation collection_id does not match collection")
        fence = current_lease_fence(record.collection_id)

        try:
            async with self._pool.connection() as conn:
                async with conn.transaction():
                    cursor = await conn.execute(
                        "SELECT status FROM argus.collections "
                        "WHERE collection_id=%s FOR UPDATE",
                        (record.collection_id,),
                    )
                    collection_row = await cursor.fetchone()
                    if collection_row is None:
                        raise RuntimeError("collection disappeared before task commit")
                    if str(collection_row["status"]) == "cancelled":
                        if fence is not None:
                            raise LeaseLostError(
                                f"collection {record.collection_id} is no longer writable"
                            )
                        raise RuntimeError("collection was cancelled before task commit")

                    if fence is not None:
                        cursor = await conn.execute(
                            """
                            SELECT worker_id
                            FROM argus.collection_leases
                            WHERE collection_id=%s
                              AND worker_id=%s
                              AND lease_until > NOW()
                            FOR UPDATE
                            """,
                            (record.collection_id, fence.worker_id),
                        )
                        lease_row = await cursor.fetchone()
                        if lease_row is None:
                            raise LeaseLostError(
                                f"worker {fence.worker_id} no longer owns lease for "
                                f"{record.collection_id}"
                            )

                    for snapshot in snapshots:
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

                    for observation in observations:
                        await conn.execute(
                            """
                            INSERT INTO argus.observations(
                              observation_id, collection_id, body
                            ) VALUES(%s, %s, %s)
                            ON CONFLICT (observation_id) DO UPDATE
                            SET collection_id=EXCLUDED.collection_id,
                                body=EXCLUDED.body
                            """,
                            (
                                observation.observation_id,
                                record.collection_id,
                                Jsonb(observation.model_dump(mode="json")),
                            ),
                        )

                    for item in evidence:
                        await conn.execute(
                            """
                            INSERT INTO argus.evidence(evidence_id, collection_id, body)
                            VALUES(%s, %s, %s)
                            ON CONFLICT (evidence_id) DO UPDATE
                            SET collection_id=EXCLUDED.collection_id,
                                body=EXCLUDED.body
                            """,
                            (
                                item.evidence_id,
                                record.collection_id,
                                Jsonb(item.model_dump(mode="json")),
                            ),
                        )

                    cursor = await conn.execute(
                        """
                        UPDATE argus.collections
                        SET status=%s, body=%s, updated_at=%s
                        WHERE collection_id=%s AND status <> 'cancelled'
                        """,
                        (
                            record.status.value,
                            Jsonb(record.model_dump(mode="json")),
                            record.updated_at,
                            record.collection_id,
                        ),
                    )
                    if cursor.rowcount != 1:
                        if fence is not None:
                            raise LeaseLostError(
                                f"worker {fence.worker_id} lost collection write authority"
                            )
                        raise RuntimeError("collection task commit was rejected")
        except (LeaseLostError, WorkerStorageError):
            raise
        except Exception as exc:
            if fence is not None:
                self._abort_storage_attempt(fence, "commit_task_success", exc)
            raise

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

        try:
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
        except Exception as exc:
            self._abort_storage_attempt(fence, f"upsert_{table}", exc)
        if cursor.rowcount != 1:
            raise LeaseLostError(
                f"worker {fence.worker_id} no longer owns lease for {collection_id}"
            )

    async def list_observations(self, collection_id: str):
        fence = current_lease_fence(collection_id)
        if fence is None:
            return await super().list_observations(collection_id)
        try:
            return await super().list_observations(collection_id)
        except Exception as exc:
            self._abort_storage_attempt(fence, "list_observations", exc)

    async def list_evidence(self, collection_id: str):
        fence = current_lease_fence(collection_id)
        if fence is None:
            return await super().list_evidence(collection_id)
        try:
            return await super().list_evidence(collection_id)
        except Exception as exc:
            self._abort_storage_attempt(fence, "list_evidence", exc)

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

        try:
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
        except Exception as exc:
            self._abort_storage_attempt(fence, "add_snapshot", exc)
        if existing is not None:
            return
        raise LeaseLostError(
            f"worker {fence.worker_id} no longer owns lease for {normalized_collection_id}"
        )

    async def latest_snapshot(self, source_url: str) -> Snapshot | None:
        fence = active_lease_fence()
        if fence is None:
            return await super().latest_snapshot(source_url)
        try:
            return await super().latest_snapshot(source_url)
        except Exception as exc:
            self._abort_storage_attempt(fence, "latest_snapshot", exc)

    async def save_recipe(self, recipe: SiteRecipe) -> None:
        fence = active_lease_fence()
        if fence is None:
            await super().save_recipe(recipe)
            return
        try:
            await super().save_recipe(recipe)
        except Exception as exc:
            self._abort_storage_attempt(fence, "save_recipe", exc)

    async def get_recipe(self, domain: str, goal: str) -> SiteRecipe | None:
        fence = active_lease_fence()
        if fence is None:
            return await super().get_recipe(domain, goal)
        try:
            return await super().get_recipe(domain, goal)
        except Exception as exc:
            self._abort_storage_attempt(fence, "get_recipe", exc)
