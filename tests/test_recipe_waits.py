import pytest

from argus.recipes.executor import PlaywrightRecipeExecutor, RecipeExecutionError
from argus.recipes.models import RecipeStep, SiteRecipe


class FakeGuard:
    async def validate(self, url):
        return url

    async def validate_redirect(self, from_url, to_url):
        del from_url
        return to_url


class FakePage:
    def __init__(self):
        self.load_states = []
        self.timeouts = []

    async def wait_for_load_state(self, state, timeout):
        self.load_states.append((state, timeout))

    async def wait_for_timeout(self, milliseconds):
        self.timeouts.append(milliseconds)


@pytest.mark.asyncio
async def test_recipe_wait_can_wait_for_load_state():
    page = FakePage()
    recipe = SiteRecipe(
        domain="example.com",
        goal="test",
        steps=[
            RecipeStep(
                action="wait",
                data={"state": "domcontentloaded", "timeout_ms": 2500},
            )
        ],
    )
    await PlaywrightRecipeExecutor(FakeGuard()).execute(page, recipe)
    assert page.load_states == [("domcontentloaded", 2500)]
    assert page.timeouts == []


@pytest.mark.asyncio
async def test_recipe_wait_rejects_unknown_load_state():
    page = FakePage()
    recipe = SiteRecipe(
        domain="example.com",
        goal="test",
        steps=[RecipeStep(action="wait", data={"state": "magic"})],
    )
    with pytest.raises(RecipeExecutionError):
        await PlaywrightRecipeExecutor(FakeGuard()).execute(page, recipe)
