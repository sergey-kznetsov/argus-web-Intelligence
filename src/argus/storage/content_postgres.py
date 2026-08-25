from __future__ import annotations

from argus.contracts.models import Observation
from argus.storage.fenced_postgres import FencedPostgresRepository
from argus.storage.lease_fencing import current_lease_fence


class ContentAwareFencedPostgresRepository(FencedPostgresRepository):
    """Production PostgreSQL repository with indexed committed-content lookup.

    Duplicate detection only reads rows already committed by a previous successful
    source task. This keeps recovery deterministic: an uncommitted task can never
    poison a process-local duplicate cache.
    """

    async def find_observation_by_content_hash(
        self,
        collection_id: str,
        *,
        content_hash: str,
        source_kinds: list[str],
    ) -> Observation | None:
        normalized_hash = content_hash.strip()
        normalized_kinds = sorted({item.strip() for item in source_kinds if item.strip()})
        if not normalized_hash or not normalized_kinds:
            return None

        fence = current_lease_fence(collection_id)
        try:
            async with self._pool.connection() as conn:
                cursor = await conn.execute(
                    """
                    SELECT body
                    FROM argus.observations
                    WHERE collection_id=%s
                      AND body ->> 'content_hash'=%s
                      AND body ->> 'source_kind' = ANY(%s)
                    ORDER BY observation_id ASC
                    LIMIT 1
                    """,
                    (collection_id, normalized_hash, normalized_kinds),
                )
                row = await cursor.fetchone()
        except Exception as exc:
            if fence is not None:
                self._raise_storage_failure(fence, "find_observation_by_content_hash", exc)
            raise
        return Observation.model_validate(row["body"]) if row else None
