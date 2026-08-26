from __future__ import annotations

from argus.crawler.agent.base import AgentTask
from argus.crawler.models import FetchResult
from argus.security.urls import UnsafeUrlError
from argus.sources.base import SourceTask
from argus.sources.duplicate_web import DuplicateAwareWebAdapter


class LifecycleRecipeWebAdapter(DuplicateAwareWebAdapter):
    """Apply bounded SiteRecipe and AGENT lifecycle around the factual web stack."""

    max_agent_direct_replay_urls = 2
    max_agent_recipe_steps = 40

    async def fetch(self, task: SourceTask) -> FetchResult:
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
                    await self.recipes.mark_failure(
                        recipe,
                        reason=f"replay_failed:{type(exc).__name__}",
                    )

        try:
            result = await self.fast.fetch(task.url)
            if result.blocked or self._needs_browser(result.text):
                return await self._browser_or_agent(task, context_fetch=result)
            return result
        except UnsafeUrlError:
            raise
        except Exception:
            return await self._browser_or_agent(task)

    async def _browser_or_agent(
        self,
        task: SourceTask,
        *,
        context_fetch: FetchResult | None = None,
    ) -> FetchResult:
        try:
            return await self.browser.fetch(task.url)
        except UnsafeUrlError:
            raise
        except Exception as browser_error:
            if self.agent is not None:
                guided = await self._agent_guided_fetch(task, context_fetch=context_fetch)
                if guided is not None:
                    return guided
            raise browser_error

    async def _agent_guided_fetch(
        self,
        task: SourceTask,
        *,
        context_fetch: FetchResult | None = None,
    ) -> FetchResult | None:
        if self.agent is None:
            return None
        goals = self._research_goals(task)
        goal_text = ", ".join(goals)
        context: dict[str, object] = {
            "allowed_domains": task.metadata.get("allowed_domains", []),
            "research_goals": goals,
            "research_input_candidates": task.metadata.get("research_input_candidates", []),
        }
        if context_fetch is not None and not context_fetch.blocked:
            context.update(
                {
                    "page_html": context_fetch.text,
                    "page_url": context_fetch.final_url,
                    "page_runtime": context_fetch.runtime,
                }
            )
        try:
            agent_result = await self.agent.run(
                AgentTask(
                    url=task.url,
                    goal=goal_text,
                    instruction=(
                        f"Find the public page or view needed for goals '{goal_text}'. Use public site "
                        "navigation, search, filters and expandable sections when needed."
                    ),
                    context=context,
                )
            )
        except UnsafeUrlError:
            raise
        except Exception as exc:
            task.metadata["agent_error"] = "agent runtime unavailable"
            task.metadata["agent_execution"] = {
                "status": "failed",
                "code": "AGENT_RUNTIME_UNAVAILABLE",
                "backend": getattr(self.agent, "name", "unknown"),
                "error_type": type(exc).__name__,
                "retryable": True,
            }
            return None

        task.metadata["agent_execution"] = dict(agent_result.metadata)
        if agent_result.error:
            task.metadata["agent_error"] = agent_result.error

        if agent_result.blocked:
            return FetchResult(
                url=task.url,
                final_url=task.url,
                status_code=0,
                content_type=None,
                text="",
                blocked=True,
                runtime=f"agent:{self.agent.name}",
                metadata={
                    "agent_backend": self.agent.name,
                    "agent_error": agent_result.error,
                    "agent_execution": dict(agent_result.metadata),
                },
            )
        if not agent_result.success:
            return None

        if agent_result.actions:
            if self.recipes is None:
                task.metadata["agent_path_rejected"] = "recipe_manager_unavailable"
                return None
            compiled_steps = self.recipe_compiler.compile(agent_result.actions)
            if not compiled_steps:
                task.metadata["agent_path_rejected"] = "actions_not_deterministically_compilable"
                return None
            steps = await self._recipe_steps_for_context(
                task,
                context_fetch,
                compiled_steps,
            )
            if steps is None:
                return None

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
                return None

            if replayed.blocked:
                rejected = self.recipes.reject_candidate(
                    candidate,
                    reason="verification_blocked",
                )
                replayed.metadata.update(
                    {
                        "agent_backend": self.agent.name,
                        "agent_compiled_recipe": False,
                        "agent_execution": dict(agent_result.metadata),
                        "recipe_candidate_rejected": self.recipes.lifecycle(rejected),
                    }
                )
                return replayed

            await self.recipes.mark_success(candidate)
            replayed.metadata.update(
                {
                    "agent_backend": self.agent.name,
                    "agent_compiled_recipe": True,
                    "agent_execution": dict(agent_result.metadata),
                    "recipe_lifecycle": self.recipes.lifecycle(candidate),
                }
            )
            return replayed

        replay_candidates = [
            url for url in reversed(agent_result.visited_urls) if url and url != task.url
        ][: self.max_agent_direct_replay_urls]
        for visited in replay_candidates:
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
                    "agent_execution": dict(agent_result.metadata),
                    "agent_direct_replay_bounded": True,
                }
            )
            return fetched
        return None

    async def _recipe_steps_for_context(
        self,
        task: SourceTask,
        context_fetch: FetchResult | None,
        compiled_steps,
    ):
        """Extend only the exact verified recipe that produced the analyzed DOM."""
        steps = list(compiled_steps)
        if self.recipes is None or context_fetch is None:
            return steps
        context_recipe_id = context_fetch.metadata.get("recipe_id")
        if not isinstance(context_recipe_id, str) or not context_recipe_id:
            return steps

        active = await self.recipes.get(task.url, task.goal)
        if active is None or active.recipe_id != context_recipe_id:
            return steps
        combined = [*active.steps, *steps]
        if len(combined) > self.max_agent_recipe_steps:
            task.metadata["agent_path_rejected"] = "extended_recipe_step_budget_exceeded"
            task.metadata["agent_recipe_extension"] = {
                "base_recipe_id": active.recipe_id,
                "base_version": active.version,
                "base_steps": len(active.steps),
                "new_steps": len(steps),
                "max_steps": self.max_agent_recipe_steps,
                "accepted": False,
            }
            return None
        task.metadata["agent_recipe_extension"] = {
            "base_recipe_id": active.recipe_id,
            "base_version": active.version,
            "base_steps": len(active.steps),
            "new_steps": len(steps),
            "combined_steps": len(combined),
            "max_steps": self.max_agent_recipe_steps,
            "accepted": True,
        }
        return combined

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
                "verified_recipe_extension": True,
                "max_agent_recipe_steps": self.max_agent_recipe_steps,
            }
        if self.agent is not None:
            payload["agent_execution"] = {
                "backend": self.agent.name,
                "last_resort": True,
                "agent_output_is_evidence": False,
                "successful_action_paths_require_verified_recipe": True,
                "max_direct_replay_urls": self.max_agent_direct_replay_urls,
                "max_recipe_steps": self.max_agent_recipe_steps,
                "max_steps": getattr(self.agent, "max_steps", None),
                "timeout_seconds": getattr(self.agent, "timeout_seconds", None),
                "max_actions": getattr(self.agent, "max_actions", None),
                "max_visited_urls": getattr(self.agent, "max_visited_urls", None),
            }
        return payload
