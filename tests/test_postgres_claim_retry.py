from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import pytest

from argus.storage.postgres import PostgresRepository


class _Cursor:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row

    async def fetchone(self) -> dict[str, Any] | None:
        return self._row


class _Connection:
    def __init__(self, rows: list[dict[str, Any] | None]) -> None:
        self._rows = iter(rows)
        self.executed: list[tuple[str, object]] = []
        self.transactions = 0

    @asynccontextmanager
    async def transaction(self):
        self.transactions += 1
        yield

    async def execute(self, query: str, params: object = None) -> _Cursor:
        self.executed.append((query, params))
        return _Cursor(next(self._rows))


class _Pool:
    def __init__(self, connection: _Connection) -> None:
        self._connection = connection

    @asynccontextmanager
    async def connection(self):
        yield self._connection


@pytest.mark.asyncio
async def test_claim_retries_after_concurrent_lease_wins() -> None:
    connection = _Connection(
        [
            {"collection_id": "candidate-a"},
            None,
            {"collection_id": "candidate-b"},
            {"collection_id": "candidate-b"},
        ]
    )
    repository = object.__new__(PostgresRepository)
    repository._pool = _Pool(connection)

    claimed = await repository.claim_next_collection("worker-a", lease_seconds=30)

    assert claimed == "candidate-b"
    assert connection.transactions == 2
    assert len(connection.executed) == 4
    assert "RETURNING collection_id" in connection.executed[1][0]


@pytest.mark.asyncio
async def test_claim_returns_none_when_queue_has_no_candidate() -> None:
    connection = _Connection([None])
    repository = object.__new__(PostgresRepository)
    repository._pool = _Pool(connection)

    claimed = await repository.claim_next_collection("worker-a", lease_seconds=30)

    assert claimed is None
    assert connection.transactions == 1
    assert len(connection.executed) == 1
