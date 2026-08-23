from __future__ import annotations

import os
from uuid import uuid4

import psycopg
import pytest

from argus.contracts.models import CollectionRecord, CollectionRequest, CollectionStatus, utcnow
from argus.recipes.models import RecipeStep, SiteRecipe
from argus.storage.fenced_postgres import FencedPostgresRepository
from argus.storage.lease_fencing import LeaseLostError, lease_fence
from argus.storage.postgres_migrations import run_postgres_migrations


def postgres_dsn() -> str:
    value = os.getenv("ARGUS_TEST_POSTGRES_DSN", "").strip()
    if not value:
        pytest.skip("ARGUS_TEST_POSTGRES_DSN is not configured")
    return value


def collection(collection_id: str) -> CollectionRecord:
    timestamp = utcnow()
    return CollectionRecord(
        collection_id=collection_id,
        request=CollectionRequest(
            consumer="recipe-fencing-test",
            analysis_id=f"analysis-{uuid4()}",
            territory={"city": "Ижевск"},
            intents=["public_mentions"],
        ),
        status=CollectionStatus.RUNNING,
        stage="collecting",
        created_at=timestamp,
        updated_at=timestamp,
    )


@pytest.mark.asyncio
async def test_stale_worker_cannot_mutate_recipe_after_lease_transfer():
    dsn = postgres_dsn()
    await run_postgres_migrations(dsn)
    repository = FencedPostgresRepository(
        dsn,
        min_size=1,
        max_size=3,
        timeout_seconds=10,
        max_waiting=4,
    )
    await repository.initialize()

    collection_id = f"recipe-fence-{uuid4()}"
    worker_a = f"worker-a-{uuid4()}"
    worker_b = f"worker-b-{uuid4()}"
    domain = f"{uuid4().hex}.example.com"
    recipe = SiteRecipe(
        domain=domain,
        goal="public_mentions",
        version=1,
        steps=[RecipeStep(action="goto", value=f"https://{domain}/")],
    )

    try:
        await repository.register_worker(worker_a, metadata={"test": True})
        await repository.register_worker(worker_b, metadata={"test": True})
        await repository.create_collection(collection(collection_id))
        assert await repository.claim_next_collection(
            worker_a,
            lease_seconds=30,
        ) == collection_id

        with lease_fence(collection_id, worker_a):
            await repository.save_recipe(recipe)

        saved = await repository.get_recipe(domain, "public_mentions")
        assert saved is not None
        assert saved.failures == 0

        async with repository._pool.connection() as conn:
            await conn.execute(
                """
                UPDATE argus.collection_leases
                SET lease_until=NOW() - INTERVAL '1 second'
                WHERE collection_id=%s AND worker_id=%s
                """,
                (collection_id, worker_a),
            )
        assert await repository.claim_next_collection(
            worker_b,
            lease_seconds=30,
        ) == collection_id

        stale = recipe.model_copy(deep=True)
        stale.failures = 99
        with lease_fence(collection_id, worker_a):
            with pytest.raises(LeaseLostError):
                await repository.save_recipe(stale)

        unchanged = await repository.get_recipe(domain, "public_mentions")
        assert unchanged is not None
        assert unchanged.failures == 0

        current = recipe.model_copy(deep=True)
        current.failures = 1
        with lease_fence(collection_id, worker_b):
            await repository.save_recipe(current)

        updated = await repository.get_recipe(domain, "public_mentions")
        assert updated is not None
        assert updated.failures == 1
    finally:
        await repository.unregister_worker(worker_a)
        await repository.unregister_worker(worker_b)
        with psycopg.connect(dsn) as conn:
            conn.execute(
                "DELETE FROM argus.site_recipes WHERE domain=%s",
                (domain,),
            )
            conn.execute(
                "DELETE FROM argus.collections WHERE collection_id=%s",
                (collection_id,),
            )
        await repository.close()
