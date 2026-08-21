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
                await self.url_guard.validate_redirect(step.value, page.url)
            elif step.action == "click":
                await page.locator(self._selector(step)).click()
            elif step.action == "fill":
                await page.locator(self._selector(step)).fill(step.value or "")
            elif step.action == "press":
                await page.locator(self._selector(step)).press(step.value or "Enter")
            elif step.action == "wait":
                await self._wait(page, step.data)
            elif step.action == "scroll":
                amount = self._bounded_int(step.data.get("pixels", 1200), -100_000, 100_000)
                await page.evaluate("pixels => window.scrollBy(0, pixels)", amount)
            elif step.action == "extract":
                locator = page.locator(self._selector(step))
                texts = await locator.all_inner_texts()
                max_items = self._bounded_int(step.data.get("max_items", 100), 1, 500)
                max_chars = self._bounded_int(step.data.get("max_chars", 5_000), 100, 20_000)
                extracted.append(
                    {
                        "selector": step.selector,
                        "text": [str(text)[:max_chars] for text in texts[:max_items]],
                    }
                )
        return extracted

    async def _wait(self, page: Any, data: dict[str, Any]) -> None:
        state = str(data.get("state") or "").strip().lower()
        if state:
            allowed_states = {"load", "domcontentloaded", "networkidle"}
            if state not in allowed_states:
                raise RecipeExecutionError("recipe wait state is invalid")
            timeout_ms = self._bounded_int(data.get("timeout_ms", 10_000), 250, 60_000)
            await page.wait_for_load_state(state, timeout=timeout_ms)
            return
        milliseconds = self._bounded_int(data.get("milliseconds", 500), 0, 10_000)
        await page.wait_for_timeout(milliseconds)

    @staticmethod
    def _selector(step: Any) -> str:
        if not step.selector:
            raise RecipeExecutionError(f"{step.action} requires selector")
        return step.selector

    @staticmethod
    def _bounded_int(value: Any, minimum: int, maximum: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise RecipeExecutionError("recipe numeric value is invalid") from exc
        return max(minimum, min(maximum, parsed))
