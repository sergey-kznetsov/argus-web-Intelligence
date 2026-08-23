from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator


@dataclass(frozen=True, slots=True)
class LeaseFence:
    collection_id: str
    worker_id: str


class LeaseLostError(asyncio.CancelledError):
    """The worker no longer owns the collection lease and must stop mutating it."""


class WorkerStorageError(asyncio.CancelledError):
    """A lease-owned storage operation failed; abort this attempt for safe replay."""


_CURRENT_LEASE_FENCE: ContextVar[LeaseFence | None] = ContextVar(
    "argus_current_lease_fence",
    default=None,
)


def active_lease_fence() -> LeaseFence | None:
    return _CURRENT_LEASE_FENCE.get()


def current_lease_fence(collection_id: str) -> LeaseFence | None:
    fence = active_lease_fence()
    if fence is None or fence.collection_id != collection_id:
        return None
    return fence


@contextmanager
def lease_fence(collection_id: str, worker_id: str) -> Iterator[LeaseFence]:
    collection = collection_id.strip()
    worker = worker_id.strip()
    if not collection or not worker:
        raise ValueError("collection_id and worker_id are required for lease fencing")
    fence = LeaseFence(collection_id=collection, worker_id=worker)
    token = _CURRENT_LEASE_FENCE.set(fence)
    try:
        yield fence
    finally:
        _CURRENT_LEASE_FENCE.reset(token)
