from __future__ import annotations

from argus.contracts.models import CollectionRecord, Evidence, Observation, Snapshot
from argus.storage.sqlite import SQLiteRepository


class AtomicSQLiteRepository(SQLiteRepository):
    """SQLite repository with one-transaction factual task persistence for embedded mode."""

    async def commit_task_success(
        self,
        record: CollectionRecord,
        *,
        observations: list[Observation],
        evidence: list[Evidence],
        snapshots: list[Snapshot],
    ) -> None:
        await self._run(
            self._commit_task_success_sync,
            record,
            observations,
            evidence,
            snapshots,
        )

    def _commit_task_success_sync(
        self,
        record: CollectionRecord,
        observations: list[Observation],
        evidence: list[Evidence],
        snapshots: list[Snapshot],
    ) -> None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT status FROM collections WHERE collection_id=?",
                (record.collection_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError("collection disappeared before task commit")
            if str(row["status"]) == "cancelled":
                raise RuntimeError("collection was cancelled before task commit")

            for snapshot in snapshots:
                conn.execute(
                    "INSERT OR IGNORE INTO snapshots(snapshot_id,source_url,collected_at,body) "
                    "VALUES(?,?,?,?)",
                    (
                        snapshot.snapshot_id,
                        snapshot.source_url,
                        snapshot.collected_at.isoformat(),
                        snapshot.model_dump_json(),
                    ),
                )

            for observation in observations:
                conn.execute(
                    "INSERT OR REPLACE INTO observations(observation_id,collection_id,body) "
                    "VALUES(?,?,?)",
                    (
                        observation.observation_id,
                        observation.collection_id,
                        observation.model_dump_json(),
                    ),
                )

            for item in evidence:
                conn.execute(
                    "INSERT OR REPLACE INTO evidence(evidence_id,collection_id,body) "
                    "VALUES(?,?,?)",
                    (
                        item.evidence_id,
                        record.collection_id,
                        item.model_dump_json(),
                    ),
                )

            cursor = conn.execute(
                "UPDATE collections SET status=?,body=?,updated_at=? "
                "WHERE collection_id=? AND status<>'cancelled'",
                (
                    record.status.value,
                    record.model_dump_json(),
                    record.updated_at.isoformat(),
                    record.collection_id,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("collection task commit was rejected")
