from __future__ import annotations

from typing import Any

from argus.recipes.models import SiteRecipe
from argus.security.urls import UrlGuard


class RecipeExecutionError(RuntimeError):
    pass


class PlaywrightRecipeExecutor:
    def __init__(self, url_guard: UrlGuard) -> None:
        self.url_guard = url_guard

    async def execute(self, page: Any, recipe: SiteRecipe) -> list[dict[str, Any]]:
        extracted: list[dict[str, Any]] = []
        for step in recipe.steps:
            if step.action == "goto":
                if not step.value:
                    raise RecipeExecutionError("goto requires value")
                await self.url_guard.validate(step.value)
                await page.goto(step.value)
            elif step.action == "click":
                await page.locator(self._selector(step)).click()
            elif step.action == "fill":
                await page.locator(self._selector(step)).fill(step.value or "")
            elif step.action == "press":
                await page.locator(self._selector(step)).press(step.value or "Enter")
            elif step.action == "wait":
                await page.wait_for_timeout(int(step.data.get("milliseconds", 500)))
            elif step.action == "scroll":
                amount = int(step.data.get("pixels", 1200))
                await page.evaluate("pixels => window.scrollBy(0, pixels)", amount)
            elif step.action == "extract":
                locator = page.locator(self._selector(step))
                extracted.append({"selector": step.selector, "text": await locator.all_inner_texts()})
        return extracted

    @staticmethod
    def _selector(step: Any) -> str:
        if not step.selector:
            raise RecipeExecutionError(f"{step.action} requires selector")
        return step.selector
