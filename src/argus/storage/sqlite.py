from __future__ import annotations

import asyncio
import sqlite3
from datetime import timedelta
from pathlib import Path
from typing import Any

from argus.contracts.models import CollectionRecord, Evidence, Observation, Snapshot
from argus.recipes.models import SiteRecipe
from argus.storage.base import IdempotencyConflictError

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS collections (
  collection_id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  body TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_collections_status ON collections(status);
CREATE TABLE IF NOT EXISTS collection_idempotency (
  idempotency_key TEXT PRIMARY KEY,
  collection_id TEXT NOT NULL UNIQUE,
  request_hash TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(collection_id) REFERENCES collections(collection_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS observations (
  observation_id TEXT PRIMARY KEY,
  collection_id TEXT NOT NULL,
  body TEXT NOT NULL,
  FOREIGN KEY(collection_id) REFERENCES collections(collection_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_observations_collection ON observations(collection_id);
CREATE TABLE IF NOT EXISTS evidence (
  evidence_id TEXT PRIMARY KEY,
  collection_id TEXT NOT NULL,
  body TEXT NOT NULL,
  FOREIGN KEY(collection_id) REFERENCES collections(collection_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_evidence_collection ON evidence(collection_id);
CREATE TABLE IF NOT EXISTS snapshots (
  snapshot_id TEXT PRIMARY KEY,
  source_url TEXT NOT NULL,
  collected_at TEXT NOT NULL,
  body TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_snapshots_url_time ON snapshots(source_url, collected_at DESC);
CREATE TABLE IF NOT EXISTS site_recipes (
  recipe_id TEXT PRIMARY KEY,
  domain TEXT NOT NULL,
  goal TEXT NOT NULL,
  version INTEGER NOT NULL,
  body TEXT NOT NULL,
  UNIQUE(domain, goal, version)
);
CREATE INDEX IF NOT EXISTS ix_recipes_lookup ON site_recipes(domain, goal, version DESC);
"""


class SQLiteRepository:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._initialize_sync)

    async def close(self) -> None:
        return None

    async def health(self) -> dict[str, object]:
        try:
            await self._run(self._health_sync)
        except Exception:
            return {"status": "error", "backend": "sqlite"}
        return {"status": "ok", "backend": "sqlite"}

    def _health_sync(self) -> None:
        with self._connect() as conn:
            conn.execute("SELECT 1").fetchone()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _initialize_sync(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    async def _run(self, fn: Any, *args: Any) -> Any:
        async with self._lock:
            return await asyncio.to_thread(fn, *args)

    async def create_collection(self, record: CollectionRecord) -> None:
        await self._run(self._upsert_collection_sync, record, True)

    async def create_collection_idempotent(
        self,
        record: CollectionRecord,
        *,
        idempotency_key: str,
        request_hash: str,
        idempotency_window_seconds: int | None = None,
        max_active_collections: int | None = None,
        max_active_per_consumer: int | None = None,
    ) -> tuple[CollectionRecord, bool]:
        del max_active_collections, max_active_per_consumer
        return await self._run(
            self._create_collection_idempotent_sync,
            record,
            idempotency_key,
            request_hash,
            idempotency_window_seconds,
        )

    def _create_collection_idempotent_sync(
        self,
        record: CollectionRecord,
        idempotency_key: str,
        request_hash: str,
        idempotency_window_seconds: int | None,
    ) -> tuple[CollectionRecord, bool]:
        with self._connect() as conn:
            if idempotency_window_seconds is not None:
                cutoff = record.created_at - timedelta(
                    seconds=max(1, int(idempotency_window_seconds))
                )
                conn.execute(
                    """
                    DELETE FROM collection_idempotency
                    WHERE idempotency_key=? AND created_at<=?
                    """,
                    (idempotency_key, cutoff.isoformat()),
                )

            existing = conn.execute(
                """
                SELECT collection_id, request_hash
                FROM collection_idempotency
                WHERE idempotency_key=?
                """,
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                if str(existing["request_hash"]) != request_hash:
                    raise IdempotencyConflictError(
                        "idempotency key is already bound to another collection request"
                    )
                row = conn.execute(
                    "SELECT body FROM collections WHERE collection_id=?",
                    (str(existing["collection_id"]),),
                ).fetchone()
                if row is None:
                    raise RuntimeError("idempotency mapping references a missing collection")
                return CollectionRecord.model_validate_json(row["body"]), False

            conn.execute(
                """
                INSERT INTO collections(collection_id,status,body,created_at,updated_at)
                VALUES(?,?,?,?,?)
                """,
                (
                    record.collection_id,
                    record.status.value,
                    record.model_dump_json(),
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                ),
            )
            conn.execute(
                """
                INSERT INTO collection_idempotency(
                  idempotency_key, collection_id, request_hash, created_at
                ) VALUES(?,?,?,?)
                """,
                (
                    idempotency_key,
                    record.collection_id,
                    request_hash,
                    record.created_at.isoformat(),
                ),
            )
            return record, True

    async def update_collection(self, record: CollectionRecord) -> None:
        await self._run(self._upsert_collection_sync, record, False)

    def _upsert_collection_sync(self, record: CollectionRecord, insert_only: bool) -> None:
        sql = (
            "INSERT INTO collections(collection_id,status,body,created_at,updated_at) VALUES(?,?,?,?,?)"
            if insert_only
            else "UPDATE collections SET status=?,body=?,updated_at=? WHERE collection_id=?"
        )
        with self._connect() as conn:
            if insert_only:
                conn.execute(
                    sql,
                    (
                        record.collection_id,
                        record.status.value,
                        record.model_dump_json(),
                        record.created_at.isoformat(),
                        record.updated_at.isoformat(),
                    ),
                )
            else:
                conn.execute(
                    sql,
                    (
                        record.status.value,
                        record.model_dump_json(),
                        record.updated_at.isoformat(),
                        record.collection_id,
                    ),
                )

    async def get_collection(self, collection_id: str) -> CollectionRecord | None:
        return await self._run(self._get_collection_sync, collection_id)

    def _get_collection_sync(self, collection_id: str) -> CollectionRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT body FROM collections WHERE collection_id=?", (collection_id,)
            ).fetchone()
        return CollectionRecord.model_validate_json(row["body"]) if row else None

    async def list_recoverable_collections(self) -> list[CollectionRecord]:
        return await self._run(self._recoverable_sync)

    def _recoverable_sync(self) -> list[CollectionRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT body FROM collections WHERE status IN ('queued','running')"
            ).fetchall()
        return [CollectionRecord.model_validate_json(row["body"]) for row in rows]

    async def add_observation(self, observation: Observation) -> None:
        await self._run(
            self._insert_json_sync,
            "observations",
            "observation_id",
            observation.observation_id,
            observation.collection_id,
            observation.model_dump_json(),
        )

    async def list_observations(self, collection_id: str) -> list[Observation]:
        rows = await self._run(self._list_json_sync, "observations", collection_id)
        return [Observation.model_validate_json(row) for row in rows]

    async def add_evidence(self, evidence: Evidence, collection_id: str) -> None:
        await self._run(
            self._insert_json_sync,
            "evidence",
            "evidence_id",
            evidence.evidence_id,
            collection_id,
            evidence.model_dump_json(),
        )

    async def list_evidence(self, collection_id: str) -> list[Evidence]:
        rows = await self._run(self._list_json_sync, "evidence", collection_id)
        return [Evidence.model_validate_json(row) for row in rows]

    def _insert_json_sync(
        self, table: str, id_col: str, item_id: str, collection_id: str, body: str
    ) -> None:
        if table not in {"observations", "evidence"} or id_col not in {
            "observation_id",
            "evidence_id",
        }:
            raise ValueError("invalid table")
        with self._connect() as conn:
            conn.execute(
                f"INSERT OR REPLACE INTO {table}({id_col},collection_id,body) VALUES(?,?,?)",
                (item_id, collection_id, body),
            )

    def _list_json_sync(self, table: str, collection_id: str) -> list[str]:
        if table not in {"observations", "evidence"}:
            raise ValueError("invalid table")
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT body FROM {table} WHERE collection_id=?", (collection_id,)
            ).fetchall()
        return [row["body"] for row in rows]

    async def add_snapshot(self, snapshot: Snapshot) -> None:
        await self._run(self._add_snapshot_sync, snapshot)

    def _add_snapshot_sync(self, snapshot: Snapshot) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO snapshots(snapshot_id,source_url,collected_at,body) VALUES(?,?,?,?)",
                (
                    snapshot.snapshot_id,
                    snapshot.source_url,
                    snapshot.collected_at.isoformat(),
                    snapshot.model_dump_json(),
                ),
            )

    async def latest_snapshot(self, source_url: str) -> Snapshot | None:
        return await self._run(self._latest_snapshot_sync, source_url)

    def _latest_snapshot_sync(self, source_url: str) -> Snapshot | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT body FROM snapshots WHERE source_url=? ORDER BY collected_at DESC LIMIT 1",
                (source_url,),
            ).fetchone()
        return Snapshot.model_validate_json(row["body"]) if row else None

    async def save_recipe(self, recipe: SiteRecipe) -> None:
        await self._run(self._save_recipe_sync, recipe)

    def _save_recipe_sync(self, recipe: SiteRecipe) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO site_recipes(recipe_id,domain,goal,version,body) VALUES(?,?,?,?,?)",
                (
                    recipe.recipe_id,
                    recipe.domain,
                    recipe.goal,
                    recipe.version,
                    recipe.model_dump_json(),
                ),
            )

    async def get_recipe(self, domain: str, goal: str) -> SiteRecipe | None:
        return await self._run(self._get_recipe_sync, domain, goal)

    def _get_recipe_sync(self, domain: str, goal: str) -> SiteRecipe | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT body FROM site_recipes WHERE domain=? AND goal=? ORDER BY version DESC LIMIT 1",
                (domain, goal),
            ).fetchone()
        return SiteRecipe.model_validate_json(row["body"]) if row else None
