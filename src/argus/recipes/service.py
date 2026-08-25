from __future__ import annotations

from datetime import timedelta
from urllib.parse import urlsplit

from argus.contracts.models import utcnow
from argus.recipes.models import RecipeStep, SiteRecipe
from argus.storage.base import Repository


class RecipeManager:
    """Manage verified deterministic browser recipes and their bounded lifecycle."""

    def __init__(
        self,
        repository: Repository,
        *,
        failure_threshold: int = 3,
        max_age_days: int = 30,
        keep_versions: int = 10,
    ) -> None:
        self.repository = repository
        self.failure_threshold = max(1, int(failure_threshold))
        self.max_age_days = max(1, int(max_age_days))
        self.keep_versions = max(1, int(keep_versions))

    async def get(self, url: str, goal: str) -> SiteRecipe | None:
        recipe = await self.repository.get_recipe(self._domain(url), goal)
        if recipe is None or recipe.status != "active":
            return None
        if self._expired(recipe):
            await self.invalidate(recipe, reason="expired")
            return None
        return recipe

    async def candidate(self, url: str, goal: str, steps: list[RecipeStep]) -> SiteRecipe:
        domain = self._domain(url)
        current = await self.repository.get_recipe(domain, goal)
        return SiteRecipe(
            domain=domain,
            goal=goal,
            version=(current.version + 1) if current else 1,
            steps=steps,
            status="candidate",
        )

    async def save(self, recipe: SiteRecipe) -> None:
        if recipe.status == "candidate":
            raise ValueError("candidate SiteRecipe must pass verified replay before persistence")
        await self.repository.save_recipe(recipe)
        await self._cleanup(recipe)

    async def mark_success(self, recipe: SiteRecipe) -> None:
        timestamp = utcnow()
        if recipe.status == "candidate":
            recipe.status = "active"
            recipe.verified_at = timestamp
        elif recipe.status == "invalidated":
            # Invalidated versions are immutable lifecycle history. Recovery requires
            # a new candidate/version, not resurrection of a stale recipe.
            raise ValueError("invalidated SiteRecipe cannot be reactivated")
        recipe.last_success_at = timestamp
        recipe.failures = 0
        recipe.successes += 1
        recipe.invalidation_reason = None
        recipe.invalidated_at = None
        await self.repository.save_recipe(recipe)
        await self._cleanup(recipe)

    async def mark_failure(self, recipe: SiteRecipe, *, reason: str = "replay_failed") -> bool:
        if recipe.status == "candidate":
            # A candidate is persisted only after verified replay. Rejected candidates
            # remain ephemeral so they cannot hide the last known persisted version.
            return False
        if recipe.status == "invalidated":
            return True
        timestamp = utcnow()
        recipe.failures += 1
        recipe.total_failures += 1
        recipe.last_failure_at = timestamp
        invalidated = recipe.failures >= self.failure_threshold
        if invalidated:
            recipe.status = "invalidated"
            recipe.invalidated_at = timestamp
            recipe.invalidation_reason = reason[:256]
        await self.repository.save_recipe(recipe)
        await self._cleanup(recipe)
        return invalidated

    async def invalidate(self, recipe: SiteRecipe, *, reason: str) -> None:
        if recipe.status == "invalidated":
            return
        recipe.status = "invalidated"
        recipe.invalidated_at = utcnow()
        recipe.invalidation_reason = reason[:256]
        await self.repository.save_recipe(recipe)
        await self._cleanup(recipe)

    def reject_candidate(self, recipe: SiteRecipe, *, reason: str) -> SiteRecipe:
        """Mark an in-memory candidate rejected without persisting an unverified version."""
        if recipe.status != "candidate":
            raise ValueError("only candidate SiteRecipe can be rejected")
        recipe.status = "invalidated"
        recipe.failures = 1
        recipe.total_failures = 1
        recipe.last_failure_at = utcnow()
        recipe.invalidated_at = recipe.last_failure_at
        recipe.invalidation_reason = reason[:256]
        return recipe

    def lifecycle(self, recipe: SiteRecipe) -> dict[str, object]:
        return {
            "recipe_id": recipe.recipe_id,
            "version": recipe.version,
            "status": recipe.status,
            "verified": recipe.verified_at is not None,
            "successes": recipe.successes,
            "total_failures": recipe.total_failures,
            "consecutive_failures": recipe.failures,
            "failure_threshold": self.failure_threshold,
            "max_age_days": self.max_age_days,
            "invalidation_reason": recipe.invalidation_reason,
        }

    def _expired(self, recipe: SiteRecipe) -> bool:
        reference = recipe.last_success_at or recipe.verified_at or recipe.created_at
        return reference <= utcnow() - timedelta(days=self.max_age_days)

    async def _cleanup(self, recipe: SiteRecipe) -> None:
        await self.repository.prune_recipe_versions(
            recipe.domain,
            recipe.goal,
            keep_versions=self.keep_versions,
        )

    @staticmethod
    def _domain(url: str) -> str:
        domain = (urlsplit(url).hostname or "").lower().strip(".")
        if not domain:
            raise ValueError("recipe URL must contain a hostname")
        return domain
