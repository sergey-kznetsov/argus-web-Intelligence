from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from argus.contracts.models import utcnow
from argus.recipes.models import RecipeStep, SiteRecipe
from argus.recipes.service import RecipeManager
from argus.storage.lifecycle_sqlite import LifecycleAtomicSQLiteRepository


@pytest.mark.asyncio
async def test_legacy_recipe_defaults_to_active_and_is_replayable(tmp_path: Path):
    repo = LifecycleAtomicSQLiteRepository(tmp_path / "db.sqlite")
    await repo.initialize()
    recipe = SiteRecipe.model_validate(
        {
            "domain": "example.com",
            "goal": "reviews",
            "version": 1,
            "steps": [{"action": "click", "selector": "#reviews"}],
            "failures": 0,
        }
    )
    await repo.save_recipe(recipe)
    manager = RecipeManager(repo)

    loaded = await manager.get("https://example.com/page", "reviews")

    assert loaded is not None
    assert loaded.status == "active"
    await repo.close()


@pytest.mark.asyncio
async def test_candidate_promotes_only_after_mark_success(tmp_path: Path):
    repo = LifecycleAtomicSQLiteRepository(tmp_path / "db.sqlite")
    await repo.initialize()
    manager = RecipeManager(repo)

    candidate = await manager.candidate(
        "https://example.com/page",
        "reviews",
        [RecipeStep(action="click", selector="#reviews")],
    )
    assert candidate.status == "candidate"
    assert await repo.get_recipe("example.com", "reviews") is None

    await manager.mark_success(candidate)
    stored = await repo.get_recipe("example.com", "reviews")

    assert stored is not None
    assert stored.status == "active"
    assert stored.verified_at is not None
    assert stored.last_success_at is not None
    assert stored.successes == 1
    assert stored.failures == 0
    await repo.close()


@pytest.mark.asyncio
async def test_consecutive_failures_invalidate_recipe_and_new_candidate_advances_version(tmp_path: Path):
    repo = LifecycleAtomicSQLiteRepository(tmp_path / "db.sqlite")
    await repo.initialize()
    manager = RecipeManager(repo, failure_threshold=3)
    recipe = SiteRecipe(
        domain="example.com",
        goal="reviews",
        version=4,
        steps=[RecipeStep(action="click", selector="#reviews")],
    )
    await manager.save(recipe)

    assert await manager.mark_failure(recipe, reason="selector_missing") is False
    assert await manager.mark_failure(recipe, reason="selector_missing") is False
    assert await manager.mark_failure(recipe, reason="selector_missing") is True

    stored = await repo.get_recipe("example.com", "reviews")
    assert stored is not None
    assert stored.status == "invalidated"
    assert stored.failures == 3
    assert stored.total_failures == 3
    assert stored.invalidation_reason == "selector_missing"
    assert await manager.get("https://example.com/page", "reviews") is None

    candidate = await manager.candidate(
        "https://example.com/page",
        "reviews",
        [RecipeStep(action="click", selector="#reviews-v2")],
    )
    assert candidate.version == 5
    assert candidate.status == "candidate"
    await repo.close()


@pytest.mark.asyncio
async def test_success_resets_consecutive_failure_streak_but_preserves_totals(tmp_path: Path):
    repo = LifecycleAtomicSQLiteRepository(tmp_path / "db.sqlite")
    await repo.initialize()
    manager = RecipeManager(repo, failure_threshold=3)
    recipe = SiteRecipe(
        domain="example.com",
        goal="reviews",
        steps=[RecipeStep(action="click", selector="#reviews")],
    )
    await manager.save(recipe)
    await manager.mark_failure(recipe)
    await manager.mark_failure(recipe)

    await manager.mark_success(recipe)

    assert recipe.status == "active"
    assert recipe.failures == 0
    assert recipe.total_failures == 2
    assert recipe.successes == 1
    assert recipe.last_success_at is not None
    await repo.close()


@pytest.mark.asyncio
async def test_expired_recipe_is_persistently_invalidated(tmp_path: Path):
    repo = LifecycleAtomicSQLiteRepository(tmp_path / "db.sqlite")
    await repo.initialize()
    manager = RecipeManager(repo, max_age_days=30)
    old = utcnow() - timedelta(days=31)
    recipe = SiteRecipe(
        domain="example.com",
        goal="reviews",
        created_at=old,
        last_success_at=old,
        steps=[RecipeStep(action="click", selector="#reviews")],
    )
    await repo.save_recipe(recipe)

    assert await manager.get("https://example.com/page", "reviews") is None
    stored = await repo.get_recipe("example.com", "reviews")
    assert stored is not None
    assert stored.status == "invalidated"
    assert stored.invalidation_reason == "expired"
    assert stored.invalidated_at is not None
    await repo.close()


@pytest.mark.asyncio
async def test_recipe_cleanup_keeps_only_latest_configured_versions(tmp_path: Path):
    repo = LifecycleAtomicSQLiteRepository(tmp_path / "db.sqlite")
    await repo.initialize()
    manager = RecipeManager(repo, keep_versions=2)

    for version in range(1, 5):
        await manager.save(
            SiteRecipe(
                domain="example.com",
                goal="reviews",
                version=version,
                steps=[RecipeStep(action="click", selector=f"#reviews-{version}")],
            )
        )

    def versions() -> list[int]:
        with repo._connect() as conn:
            rows = conn.execute(
                "SELECT version FROM site_recipes WHERE domain=? AND goal=? ORDER BY version",
                ("example.com", "reviews"),
            ).fetchall()
        return [int(row["version"]) for row in rows]

    assert await repo._run(versions) == [3, 4]
    await repo.close()


def test_rejected_candidate_is_not_reactivated_or_persisted_by_manager():
    class Repo:
        pass

    manager = RecipeManager(Repo())  # type: ignore[arg-type]
    candidate = SiteRecipe(
        domain="example.com",
        goal="reviews",
        status="candidate",
        steps=[RecipeStep(action="scroll", data={"pixels": 100})],
    )

    rejected = manager.reject_candidate(candidate, reason="verification_blocked")

    assert rejected.status == "invalidated"
    assert rejected.invalidation_reason == "verification_blocked"
    assert rejected.failures == 1
    assert rejected.total_failures == 1
