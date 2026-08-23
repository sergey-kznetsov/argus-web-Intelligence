from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from argus.contracts.models import CollectionRecord, Evidence, Observation


@dataclass(frozen=True, slots=True)
class ResultStats:
    observation_count: int
    evidence_count: int
    stored_bytes: int

    @property
    def total_items(self) -> int:
        return self.observation_count + self.evidence_count


@dataclass(frozen=True, slots=True)
class ResultBundle:
    record: CollectionRecord
    stats: ResultStats
    observations: list[Observation]
    evidence: list[Evidence]


@dataclass(frozen=True, slots=True)
class ObservationSlice:
    record: CollectionRecord
    total_count: int
    items: list[Observation]
    has_more: bool


@dataclass(frozen=True, slots=True)
class EvidenceSlice:
    record: CollectionRecord
    total_count: int
    items: list[Evidence]
    has_more: bool


class ResultTooLargeError(RuntimeError):
    def __init__(self, stats: ResultStats) -> None:
        self.stats = stats
        super().__init__(
            "result requires pagination: "
            f"items={stats.total_items}, stored_bytes={stats.stored_bytes}"
        )


class ResultReadStore(Protocol):
    async def initialize(self) -> None: ...
    async def close(self) -> None: ...
    async def result_stats(
        self,
        collection_id: str,
    ) -> tuple[CollectionRecord, ResultStats] | None: ...
    async def read_bounded_result(
        self,
        collection_id: str,
        *,
        max_items: int,
        max_bytes: int,
    ) -> ResultBundle | None: ...
    async def observation_page(
        self,
        collection_id: str,
        *,
        after_id: str | None,
        limit: int,
    ) -> ObservationSlice | None: ...
    async def evidence_page(
        self,
        collection_id: str,
        *,
        after_id: str | None,
        limit: int,
    ) -> EvidenceSlice | None: ...


class SQLiteResultReadStore:
    """Consistent bounded result reads for embedded/local SQLite mode."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def result_stats(
        self,
        collection_id: str,
    ) -> tuple[CollectionRecord, ResultStats] | None:
        return await self._run(self._result_stats_sync, collection_id)

    async def read_bounded_result(
        self,
        collection_id: str,
        *,
        max_items: int,
        max_bytes: int,
    ) -> ResultBundle | None:
        return await self._run(
            self._read_bounded_result_sync,
            collection_id,
            max_items,
            max_bytes,
        )

    async def observation_page(
        self,
        collection_id: str,
        *,
        after_id: str | None,
        limit: int,
    ) -> ObservationSlice | None:
        return await self._run(
            self._observation_page_sync,
            collection_id,
            after_id,
            limit,
        )

    async def evidence_page(
        self,
        collection_id: str,
        *,
        after_id: str | None,
        limit: int,
    ) -> EvidenceSlice | None:
        return await self._run(
            self._evidence_page_sync,
            collection_id,
            after_id,
            limit,
        )

    async def _run(self, fn, *args):
        async with self._lock:
            return await asyncio.to_thread(fn, *args)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @staticmethod
    def _stats(conn: sqlite3.Connection, collection_id: str) -> ResultStats:
        row = conn.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM observations WHERE collection_id=?) AS observations,
              (SELECT COUNT(*) FROM evidence WHERE collection_id=?) AS evidence,
              COALESCE((SELECT SUM(LENGTH(body)) FROM observations WHERE collection_id=?), 0)
                + COALESCE((SELECT SUM(LENGTH(body)) FROM evidence WHERE collection_id=?), 0)
                AS stored_bytes
            """,
            (collection_id, collection_id, collection_id, collection_id),
        ).fetchone()
        return ResultStats(
            observation_count=int(row["observations"] or 0),
            evidence_count=int(row["evidence"] or 0),
            stored_bytes=int(row["stored_bytes"] or 0),
        )

    @staticmethod
    def _record(conn: sqlite3.Connection, collection_id: str) -> CollectionRecord | None:
        row = conn.execute(
            "SELECT body FROM collections WHERE collection_id=?",
            (collection_id,),
        ).fetchone()
        return CollectionRecord.model_validate_json(row["body"]) if row else None

    def _result_stats_sync(
        self,
        collection_id: str,
    ) -> tuple[CollectionRecord, ResultStats] | None:
        with self._connect() as conn:
            conn.execute("BEGIN")
            record = self._record(conn, collection_id)
            if record is None:
                return None
            stats = self._stats(conn, collection_id)
            return record, stats

    def _read_bounded_result_sync(
        self,
        collection_id: str,
        max_items: int,
        max_bytes: int,
    ) -> ResultBundle | None:
        with self._connect() as conn:
            conn.execute("BEGIN")
            record = self._record(conn, collection_id)
            if record is None:
                return None
            stats = self._stats(conn, collection_id)
            if stats.total_items > max(1, max_items) or stats.stored_bytes > max(1024, max_bytes):
                raise ResultTooLargeError(stats)
            observation_rows = conn.execute(
                """
                SELECT body FROM observations
                WHERE collection_id=?
                ORDER BY observation_id ASC
                """,
                (collection_id,),
            ).fetchall()
            evidence_rows = conn.execute(
                """
                SELECT body FROM evidence
                WHERE collection_id=?
                ORDER BY evidence_id ASC
                """,
                (collection_id,),
            ).fetchall()
            return ResultBundle(
                record=record,
                stats=stats,
                observations=[
                    Observation.model_validate_json(row["body"]) for row in observation_rows
                ],
                evidence=[Evidence.model_validate_json(row["body"]) for row in evidence_rows],
            )

    def _observation_page_sync(
        self,
        collection_id: str,
        after_id: str | None,
        limit: int,
    ) -> ObservationSlice | None:
        page_size = max(1, limit)
        with self._connect() as conn:
            conn.execute("BEGIN")
            record = self._record(conn, collection_id)
            if record is None:
                return None
            total = int(
                conn.execute(
                    "SELECT COUNT(*) FROM observations WHERE collection_id=?",
                    (collection_id,),
                ).fetchone()[0]
            )
            if after_id is None:
                rows = conn.execute(
                    """
                    SELECT body FROM observations
                    WHERE collection_id=?
                    ORDER BY observation_id ASC
                    LIMIT ?
                    """,
                    (collection_id, page_size + 1),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT body FROM observations
                    WHERE collection_id=? AND observation_id>?
                    ORDER BY observation_id ASC
                    LIMIT ?
                    """,
                    (collection_id, after_id, page_size + 1),
                ).fetchall()
            has_more = len(rows) > page_size
            return ObservationSlice(
                record=record,
                total_count=total,
                items=[
                    Observation.model_validate_json(row["body"])
                    for row in rows[:page_size]
                ],
                has_more=has_more,
            )

    def _evidence_page_sync(
        self,
        collection_id: str,
        after_id: str | None,
        limit: int,
    ) -> EvidenceSlice | None:
        page_size = max(1, limit)
        with self._connect() as conn:
            conn.execute("BEGIN")
            record = self._record(conn, collection_id)
            if record is None:
                return None
            total = int(
                conn.execute(
                    "SELECT COUNT(*) FROM evidence WHERE collection_id=?",
                    (collection_id,),
                ).fetchone()[0]
            )
            if after_id is None:
                rows = conn.execute(
                    """
                    SELECT body FROM evidence
                    WHERE collection_id=?
                    ORDER BY evidence_id ASC
                    LIMIT ?
                    """,
                    (collection_id, page_size + 1),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT body FROM evidence
                    WHERE collection_id=? AND evidence_id>?
                    ORDER BY evidence_id ASC
                    LIMIT ?
                    """,
                    (collection_id, after_id, page_size + 1),
                ).fetchall()
            has_more = len(rows) > page_size
            return EvidenceSlice(
                record=record,
                total_count=total,
                items=[Evidence.model_validate_json(row["body"]) for row in rows[:page_size]],
                has_more=has_more,
            )
