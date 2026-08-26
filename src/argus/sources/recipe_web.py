from __future__ import annotations

from argus.contracts.models import CollectionRequest
from argus.crawler.agent.base import AgentTask
from argus.crawler.models import FetchResult
from argus.recipes.models import SiteRecipe
from argus.research.intent_coverage import IntentCoverageEvaluator
from argus.security.urls import UnsafeUrlError
from argus.sources.base import SourceResult, SourceTask
from argus.sources.duplicate_web import DuplicateAwareWebAdapter


class LifecycleRecipeWebAdapter(DuplicateAwareWebAdapter):
    """Apply bounded SiteRecipe and AGENT lifecycle around the factual web stack."""

    max_agent_direct_replay_urls = 2
    max_agent_recipe_steps = 40
    max_pending_recipe_candidates = 64
    recipe_goal_coverage = IntentCoverageEvaluator()

    async def fetch(self, task: SourceTask) -> FetchResult:
        if self.recipes is not None:
            recipe = await self.recipes.get(task.url, task.goal)
            if recipe is not None:
                try:
                    result = await self.browser.fetch(task.url, recipe=recipe)
                    self._track_active_recipe_replay(task, recipe, result)
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

            self._remember_pending_candidate(task, candidate)
            replayed.metadata.update(
                {
                    "agent_backend": self.agent.name,
                    "agent_compiled_recipe": True,
                    "agent_execution": dict(agent_result.metadata),
                    "agent_recipe_goal_verification_pending": True,
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

    async def _finalize_recipe_goal_verification(
        self,
        task: SourceTask,
        request: CollectionRequest,
        result: SourceResult,
    ) -> None:
        if self.recipes is None:
            return
        goal = str(task.goal or "").strip().casefold()
        if not goal:
            return

        evidence_observation_ids = {
            item.observation_id for item in result.evidence if item.observation_id
        }
        supporting = [
            observation
            for observation in result.observations
            if observation.observation_id in evidence_observation_ids
            and self.recipe_goal_coverage.supports(observation, goal, request=request)
        ]
        supporting_recipe_ids = {
            str(observation.provenance.get("recipe_id"))
            for observation in supporting
            if observation.provenance.get("recipe_id")
        }
        verification: dict[str, object] = {
            "goal": goal,
            "source_backed": bool(supporting),
            "supporting_observations": len(supporting),
            "model_output_is_evidence": False,
        }

        active_recipe_id = task.metadata.pop("active_recipe_replay_id", None)
        if isinstance(active_recipe_id, str) and active_recipe_id:
            active = await self.recipes.get(task.url, task.goal)
            if active is not None and active.recipe_id == active_recipe_id:
                if active_recipe_id in supporting_recipe_ids:
                    await self.recipes.mark_success(active)
                    verification["active_recipe"] = "verified"
                else:
                    invalidated = await self.recipes.mark_failure(
                        active,
                        reason="semantic_goal_not_satisfied",
                    )
                    verification["active_recipe"] = (
                        "invalidated" if invalidated else "failed_goal_verification"
                    )

        candidate_ids_raw = task.metadata.pop("pending_recipe_candidate_ids", [])
        candidate_ids = (
            [str(item) for item in candidate_ids_raw]
            if isinstance(candidate_ids_raw, list)
            else []
        )
        candidate_store = self._pending_candidate_store()
        candidate_results: list[dict[str, object]] = []
        for index, candidate_id in enumerate(candidate_ids):
            candidate = candidate_store.pop(candidate_id, None)
            if candidate is None:
                continue
            is_latest = index == len(candidate_ids) - 1
            if is_latest and candidate_id in supporting_recipe_ids:
                await self.recipes.mark_success(candidate)
                candidate_results.append(
                    {
                        "recipe_id": candidate_id,
                        "version": candidate.version,
                        "status": "active",
                        "goal_verified": True,
                    }
                )
            else:
                reason = (
                    "semantic_goal_not_satisfied"
                    if is_latest
                    else "superseded_before_goal_verification"
                )
                rejected = self.recipes.reject_candidate(candidate, reason=reason)
                candidate_results.append(
                    {
                        "recipe_id": candidate_id,
                        "version": candidate.version,
                        "status": rejected.status,
                        "goal_verified": False,
                        "reason": reason,
                    }
                )
        if candidate_results:
            verification["candidates"] = candidate_results

        task.metadata["recipe_goal_verification"] = verification
        for observation in result.observations:
            observation.provenance["site_recipe_goal_verification"] = dict(verification)

    def _track_active_recipe_replay(
        self,
        task: SourceTask,
        recipe: SiteRecipe,
        result: FetchResult,
    ) -> None:
        task.metadata["active_recipe_replay_id"] = recipe.recipe_id
        task.metadata["active_recipe_replay_version"] = recipe.version
        result.metadata["recipe_goal_verification_pending"] = True

    def _remember_pending_candidate(self, task: SourceTask, candidate: SiteRecipe) -> None:
        store = self._pending_candidate_store()
        if len(store) >= self.max_pending_recipe_candidates:
            oldest = next(iter(store), None)
            if oldest is not None:
                store.pop(oldest, None)
        store[candidate.recipe_id] = candidate
        raw_ids = task.metadata.get("pending_recipe_candidate_ids")
        ids = list(raw_ids) if isinstance(raw_ids, list) else []
        if candidate.recipe_id not in ids:
            ids.append(candidate.recipe_id)
        task.metadata["pending_recipe_candidate_ids"] = ids[-self.max_pending_recipe_candidates :]

    def _pending_candidate_store(self) -> dict[str, SiteRecipe]:
        store = getattr(self, "_recipe_candidates_pending_goal_verification", None)
        if not isinstance(store, dict):
            store = {}
            self._recipe_candidates_pending_goal_verification = store
        return store

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
                "goal_evidence_verification": True,
                "candidate_persistence_after_goal_evidence": True,
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
