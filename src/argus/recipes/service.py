from __future__ import annotations

from urllib.parse import urlsplit

from argus.contracts.models import utcnow
from argus.recipes.models import RecipeStep, SiteRecipe
from argus.storage.base import Repository


class RecipeManager:
    def __init__(self, repository: Repository) -> None:
        self.repository = repository

    async def get(self, url: str, goal: str) -> SiteRecipe | None:
        return await self.repository.get_recipe(self._domain(url), goal)

    async def candidate(self, url: str, goal: str, steps: list[RecipeStep]) -> SiteRecipe:
        domain = self._domain(url)
        current = await self.repository.get_recipe(domain, goal)
        return SiteRecipe(
            domain=domain,
            goal=goal,
            version=(current.version + 1) if current else 1,
            steps=steps,
        )

    async def save(self, recipe: SiteRecipe) -> None:
        await self.repository.save_recipe(recipe)

    async def mark_success(self, recipe: SiteRecipe) -> None:
        recipe.last_success_at = utcnow()
        recipe.failures = 0
        await self.repository.save_recipe(recipe)

    async def mark_failure(self, recipe: SiteRecipe) -> None:
        recipe.failures += 1
        await self.repository.save_recipe(recipe)

    @staticmethod
    def _domain(url: str) -> str:
        domain = (urlsplit(url).hostname or "").lower().strip(".")
        if not domain:
            raise ValueError("recipe URL must contain a hostname")
        return domain
