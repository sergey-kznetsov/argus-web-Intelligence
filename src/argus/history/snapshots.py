from __future__ import annotations

import difflib
import hashlib

from argus.contracts.models import Snapshot
from argus.storage.base import Repository


def sha256_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()


class SnapshotService:
    def __init__(self, repository: Repository, extractor_version: str = "argus/0.1") -> None:
        self.repository = repository
        self.extractor_version = extractor_version

    async def capture(
        self,
        source_id: str,
        source_url: str,
        content: str,
        content_type: str | None = None,
    ) -> Snapshot:
        previous = await self.repository.latest_snapshot(source_url)
        content_hash = sha256_text(content)
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
        snapshot = Snapshot(
            source_id=source_id,
            source_url=source_url,
            content_hash=content_hash,
            extractor_version=self.extractor_version,
            content_type=content_type,
            content=content,
            previous_snapshot_id=previous.snapshot_id if previous else None,
            diff=diff,
        )
        # Every successful collection is a historical observation, even when the content is unchanged.
        # This preserves a real collected_at timestamp and guarantees that provenance references exist.
        await self.repository.add_snapshot(snapshot)
        return snapshot
