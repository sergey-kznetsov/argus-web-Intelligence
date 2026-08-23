from __future__ import annotations

import difflib
import hashlib
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Iterator

from argus import __version__
from argus.contracts.models import Snapshot
from argus.storage.base import Repository


def sha256_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()


def stable_snapshot_id(
    *,
    collection_id: str,
    source_id: str,
    source_url: str,
    content_hash: str,
    extractor_version: str,
) -> str:
    payload = "\x00".join(
        (collection_id, source_id, source_url, content_hash, extractor_version)
    )
    return "snapshot-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class SnapshotBatch:
    """Task-local snapshot buffer committed together with factual rows/checkpoint."""

    snapshots: list[Snapshot] = field(default_factory=list)
    _latest_by_url: dict[str, Snapshot] = field(default_factory=dict)

    def latest(self, source_url: str) -> Snapshot | None:
        return self._latest_by_url.get(source_url)

    def add(self, snapshot: Snapshot) -> None:
        current = self._latest_by_url.get(snapshot.source_url)
        if current is not None and current.snapshot_id == snapshot.snapshot_id:
            return
        self.snapshots.append(snapshot)
        self._latest_by_url[snapshot.source_url] = snapshot


_CURRENT_SNAPSHOT_BATCH: ContextVar[SnapshotBatch | None] = ContextVar(
    "argus_current_snapshot_batch",
    default=None,
)


def current_snapshot_batch() -> SnapshotBatch | None:
    return _CURRENT_SNAPSHOT_BATCH.get()


@contextmanager
def stage_snapshots() -> Iterator[SnapshotBatch]:
    """Buffer snapshots until the orchestrator atomically commits one source task."""

    existing = _CURRENT_SNAPSHOT_BATCH.get()
    if existing is not None:
        yield existing
        return
    batch = SnapshotBatch()
    token = _CURRENT_SNAPSHOT_BATCH.set(batch)
    try:
        yield batch
    finally:
        _CURRENT_SNAPSHOT_BATCH.reset(token)


class SnapshotService:
    def __init__(self, repository: Repository, extractor_version: str | None = None) -> None:
        self.repository = repository
        self.extractor_version = extractor_version or f"argus/{__version__}"

    async def capture(
        self,
        source_id: str,
        source_url: str,
        content: str,
        content_type: str | None = None,
        *,
        collection_id: str | None = None,
    ) -> Snapshot:
        batch = current_snapshot_batch()
        previous = batch.latest(source_url) if batch is not None else None
        if previous is None:
            previous = await self.repository.latest_snapshot(source_url)

        content_hash = sha256_text(content)
        snapshot_id = None
        normalized_collection_id = (collection_id or "").strip()
        if normalized_collection_id:
            snapshot_id = stable_snapshot_id(
                collection_id=normalized_collection_id,
                source_id=source_id,
                source_url=source_url,
                content_hash=content_hash,
                extractor_version=self.extractor_version,
            )
            if previous is not None and previous.snapshot_id == snapshot_id:
                return previous

        diff = None
        if previous and previous.content_hash != content_hash:
            diff = "\n".join(
                difflib.unified_diff(
                    previous.content.splitlines(),
                    content.splitlines(),
                    fromfile=previous.snapshot_id,
                    tofile="current",
                    lineterm="",
                )
            )
        payload: dict[str, object] = {
            "source_id": source_id,
            "source_url": source_url,
            "content_hash": content_hash,
            "extractor_version": self.extractor_version,
            "content_type": content_type,
            "content": content,
            "previous_snapshot_id": previous.snapshot_id if previous else None,
            "diff": diff,
        }
        if snapshot_id is not None:
            payload["snapshot_id"] = snapshot_id
        snapshot = Snapshot.model_validate(payload)

        if batch is not None:
            batch.add(snapshot)
        elif normalized_collection_id:
            await self.repository.add_snapshot(
                snapshot,
                collection_id=normalized_collection_id,
            )
        else:
            await self.repository.add_snapshot(snapshot)
        return snapshot
