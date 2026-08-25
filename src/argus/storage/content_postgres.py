from __future__ import annotations

from argus.contracts.models import Observation
from argus.storage.fenced_postgres import FencedPostgresRepository
from argus.storage.lease_fencing import (
    LeaseLostError,
    active_lease_fence,
    current_lease_fence,
)


class ContentAwareFencedPostgresRepository(FencedPostgresRepository):
    """Production PostgreSQL repository with content lookup and recipe retention."""

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

    async def prune_recipe_versions(
        self,
        domain: str,
        goal: str,
        *,
        keep_versions: int,
    ) -> int:
        keep = max(1, int(keep_versions))
        fence = active_lease_fence()
        try:
            async with self._pool.connection() as conn:
                async with conn.transaction():
                    if fence is not None:
                        lease_row = await (
                            await conn.execute(
                                """
                                SELECT worker_id
                                FROM argus.collection_leases
                                WHERE collection_id=%s
                                  AND worker_id=%s
                                  AND lease_until > NOW()
                                FOR UPDATE
                                """,
                                (fence.collection_id, fence.worker_id),
                            )
                        ).fetchone()
                        if lease_row is None:
                            raise LeaseLostError(
                                f"worker {fence.worker_id} no longer owns lease for "
                                f"{fence.collection_id}"
                            )
                    cursor = await conn.execute(
                        """
                        DELETE FROM argus.site_recipes
                        WHERE domain=%s AND goal=%s
                          AND version NOT IN (
                            SELECT version
                            FROM argus.site_recipes
                            WHERE domain=%s AND goal=%s
                            ORDER BY version DESC
                            LIMIT %s
                          )
                        """,
                        (domain, goal, domain, goal, keep),
                    )
                    return max(0, int(cursor.rowcount or 0))
        except LeaseLostError:
            raise
        except Exception as exc:
            if fence is not None:
                self._raise_storage_failure(fence, "prune_recipe_versions", exc)
            raise
