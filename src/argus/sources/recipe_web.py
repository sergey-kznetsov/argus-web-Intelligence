from __future__ import annotations

from argus.crawler.agent.base import AgentTask
from argus.crawler.models import FetchResult
from argus.security.urls import UnsafeUrlError
from argus.sources.base import SourceTask
from argus.sources.duplicate_web import DuplicateAwareWebAdapter


class LifecycleRecipeWebAdapter(DuplicateAwareWebAdapter):
    """Apply bounded SiteRecipe lifecycle around the existing web factual stack."""

    async def fetch(self, task: SourceTask) -> FetchResult:
        recipe_failed = False
        if self.recipes is not None:
            recipe = await self.recipes.get(task.url, task.goal)
            if recipe is not None:
                try:
                    result = await self.browser.fetch(task.url, recipe=recipe)
                    if not result.blocked:
                        await self.recipes.mark_success(recipe)
                    self._attach_recipe_lifecycle(result, recipe)
                    return result
                except UnsafeUrlError:
                    raise
                except Exception as exc:
                    recipe_failed = True
                    await self.recipes.mark_failure(
                        recipe,
                        reason=f"replay_failed:{type(exc).__name__}",
                    )

        if recipe_failed and self.agent is not None:
            guided = await self._agent_guided_fetch(task)
            if guided is not None:
                return guided

        try:
            result = await self.fast.fetch(task.url)
            if result.blocked or self._needs_browser(result.text):
                return await self._browser_or_agent(task)
            return result
        except UnsafeUrlError:
            raise
        except Exception:
            return await self._browser_or_agent(task)

    async def _agent_guided_fetch(self, task: SourceTask) -> FetchResult | None:
        if self.agent is None:
            return None
        goals = self._research_goals(task)
        goal_text = ", ".join(goals)
        agent_result = await self.agent.run(
            AgentTask(
                url=task.url,
                goal=goal_text,
                instruction=(
                    f"Find the public page or view needed for goals '{goal_text}'. Use public site "
                    "navigation, search, filters and expandable sections when needed."
                ),
                context={
                    "allowed_domains": task.metadata.get("allowed_domains", []),
                    "research_goals": goals,
                },
            )
        )
        if agent_result.blocked:
            return FetchResult(
                url=task.url,
                final_url=task.url,
                status_code=0,
                content_type=None,
                text="",
                blocked=True,
                runtime=f"agent:{self.agent.name}",
                metadata={"agent_error": agent_result.error},
            )
        if not agent_result.success:
            return None

        if self.recipes is not None and agent_result.actions:
            steps = self.recipe_compiler.compile(agent_result.actions)
            if steps:
                candidate = await self.recipes.candidate(task.url, task.goal, steps)
                try:
                    replayed = await self.browser.fetch(task.url, recipe=candidate)
                except UnsafeUrlError:
                    raise
                except Exception as exc:
                    rejected = self.recipes.reject_candidate(
                        candidate,
                        reason=f"verification_failed:{type(exc).__name__}",
                    )
                    task.metadata["recipe_candidate_rejected"] = self.recipes.lifecycle(rejected)
                else:
                    if replayed.blocked:
                        rejected = self.recipes.reject_candidate(
                            candidate,
                            reason="verification_blocked",
                        )
                        replayed.metadata.update(
                            {
                                "agent_backend": self.agent.name,
                                "agent_compiled_recipe": False,
                                "recipe_candidate_rejected": self.recipes.lifecycle(rejected),
                            }
                        )
                        # Do not try alternate URLs to sidestep a challenge encountered
                        # while verifying the deterministic recipe.
                        return replayed

                    await self.recipes.mark_success(candidate)
                    replayed.metadata.update(
                        {
                            "agent_backend": self.agent.name,
                            "agent_compiled_recipe": True,
                            "recipe_lifecycle": self.recipes.lifecycle(candidate),
                        }
                    )
                    return replayed

        for visited in reversed(agent_result.visited_urls):
            if visited == task.url:
                continue
            try:
                fetched = await self.browser.fetch(visited)
            except UnsafeUrlError:
                raise
            except Exception:
                continue
            fetched.metadata.update(
                {
                    "agent_backend": self.agent.name,
                    "agent_guided": True,
                    "agent_origin_url": task.url,
                }
            )
            return fetched
        return None

    def _attach_recipe_lifecycle(self, result: FetchResult, recipe) -> None:
        if self.recipes is None:
            return
        result.metadata["recipe_lifecycle"] = self.recipes.lifecycle(recipe)

    async def health(self) -> dict[str, object]:
        payload = dict(await super().health())
        if self.recipes is not None:
            payload["site_recipe_lifecycle"] = {
                "verified_promotion": True,
                "failure_threshold": self.recipes.failure_threshold,
                "max_age_days": self.recipes.max_age_days,
                "keep_versions": self.recipes.keep_versions,
                "blocked_verification_fallback": False,
            }
        return payload
