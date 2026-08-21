from __future__ import annotations

import asyncio
from uuid import uuid4

from argus.crawler.models import FetchResult


class FetchBroker:
    """Correlate Crawlee request unique keys with callers awaiting fetch results."""

    def __init__(self) -> None:
        self._futures: dict[str, asyncio.Future[FetchResult]] = {}

    def create(self) -> tuple[str, asyncio.Future[FetchResult]]:
        key = str(uuid4())
        future = asyncio.get_running_loop().create_future()
        self._futures[key] = future
        return key, future

    def resolve(self, key: str, result: FetchResult) -> None:
        future = self._futures.get(key)
        if future is not None and not future.done():
            future.set_result(result)

    def reject(self, key: str, error: BaseException) -> None:
        future = self._futures.get(key)
        if future is not None and not future.done():
            future.set_exception(error)

    def discard(self, key: str) -> None:
        self._futures.pop(key, None)

    def reject_all(self, error: BaseException) -> None:
        for future in self._futures.values():
            if not future.done():
                future.set_exception(error)
        self._futures.clear()
