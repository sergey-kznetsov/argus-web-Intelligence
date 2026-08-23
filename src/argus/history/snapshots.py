from __future__ import annotations

import difflib
import hashlib

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
        if normalized_collection_id:
            await self.repository.add_snapshot(
                snapshot,
                collection_id=normalized_collection_id,
            )
        else:
            await self.repository.add_snapshot(snapshot)
        return snapshot
