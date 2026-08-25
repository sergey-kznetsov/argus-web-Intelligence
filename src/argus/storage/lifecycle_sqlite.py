from __future__ import annotations

from argus.storage.atomic_sqlite import AtomicSQLiteRepository


class LifecycleAtomicSQLiteRepository(AtomicSQLiteRepository):
    """Embedded repository with bounded SiteRecipe version retention."""

    async def prune_recipe_versions(
        self,
        domain: str,
        goal: str,
        *,
        keep_versions: int,
    ) -> int:
        keep = max(1, int(keep_versions))
        return int(await self._run(self._prune_recipe_versions_sync, domain, goal, keep))

    def _prune_recipe_versions_sync(self, domain: str, goal: str, keep: int) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                DELETE FROM site_recipes
                WHERE domain=? AND goal=?
                  AND version NOT IN (
                    SELECT version
                    FROM site_recipes
                    WHERE domain=? AND goal=?
                    ORDER BY version DESC
                    LIMIT ?
                  )
                """,
                (domain, goal, domain, goal, keep),
            )
            return max(0, int(cursor.rowcount or 0))
