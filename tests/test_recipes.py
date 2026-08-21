from pathlib import Path

import pytest

from argus.recipes.models import RecipeStep, SiteRecipe
from argus.storage.sqlite import SQLiteRepository


@pytest.mark.asyncio
async def test_recipe_latest_version(tmp_path: Path):
    repo = SQLiteRepository(tmp_path / "db.sqlite")
    await repo.initialize()
    await repo.save_recipe(SiteRecipe(domain="example.com", goal="reviews", version=1,
                                      steps=[RecipeStep(action="click", selector="#reviews")]))
    await repo.save_recipe(SiteRecipe(domain="example.com", goal="reviews", version=2,
                                      steps=[RecipeStep(action="click", selector="text=Reviews")]))
    recipe = await repo.get_recipe("example.com", "reviews")
    assert recipe and recipe.version == 2
