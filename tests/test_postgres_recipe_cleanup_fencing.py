from __future__ import annotations

import os
from uuid import uuid4

import psycopg
import pytest

from argus.contracts.models import CollectionRecord, CollectionRequest, CollectionStatus, utcnow
from argus.recipes.models import RecipeStep, SiteRecipe
from argus.storage.content_postgres import ContentAwareFencedPostgresRepository
from argus.storage.lease_fencing import LeaseLostError, lease_fence
from argus.storage.postgres_migrations import run_postgres_migrations


def postgres_dsn() -> str:
    value = os.getenv("ARGUS_TEST_POSTGRES_DSN", "").strip()
    if not value:
        pytest.skip("ARGUS_TEST_POSTGRES_DSN is not configured")
    return value


def record(collection_id: str) -> CollectionRecord:
    timestamp = utcnow()
    return CollectionRecord(
        collection_id=collection_id,
        request=CollectionRequest(
            consumer="recipe-cleanup-fence-test",
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
async def test_recipe_cleanup_requires_current_worker_lease():
    dsn = postgres_dsn()
    await run_postgres_migrations(dsn)
    repository = ContentAwareFencedPostgresRepository(
        dsn,
        min_size=1,
        max_size=3,
        timeout_seconds=10,
        max_waiting=4,
    )
    await repository.initialize()
    collection_id = f"recipe-cleanup-{uuid4()}"
    worker_a = f"worker-a-{uuid4()}"
    worker_b = f"worker-b-{uuid4()}"
    domain = f"{uuid4().hex}.example.com"

    try:
        await repository.register_worker(worker_a, metadata={"test": True})
        await repository.register_worker(worker_b, metadata={"test": True})
        await repository.create_collection(record(collection_id))
        assert await repository.claim_next_collection(worker_a, lease_seconds=30) == collection_id

        with lease_fence(collection_id, worker_a):
            for version in range(1, 4):
                await repository.save_recipe(
                    SiteRecipe(
                        domain=domain,
                        goal="public_mentions",
                        version=version,
                        steps=[RecipeStep(action="goto", value=f"https://{domain}/{version}")],
                    )
                )
            assert await repository.prune_recipe_versions(
                domain,
                "public_mentions",
                keep_versions=2,
            ) == 1

        async with repository._pool.connection() as conn:
            versions = await (
                await conn.execute(
                    "SELECT version FROM argus.site_recipes WHERE domain=%s ORDER BY version",
                    (domain,),
                )
            ).fetchall()
        assert [int(row["version"]) for row in versions] == [2, 3]

        async with repository._pool.connection() as conn:
            await conn.execute(
                """
                UPDATE argus.collection_leases
                SET lease_until=NOW() - INTERVAL '1 second'
                WHERE collection_id=%s AND worker_id=%s
                """,
                (collection_id, worker_a),
            )
        assert await repository.claim_next_collection(worker_b, lease_seconds=30) == collection_id

        with lease_fence(collection_id, worker_a):
            with pytest.raises(LeaseLostError):
                await repository.prune_recipe_versions(
                    domain,
                    "public_mentions",
                    keep_versions=1,
                )

        with lease_fence(collection_id, worker_b):
            assert await repository.prune_recipe_versions(
                domain,
                "public_mentions",
                keep_versions=1,
            ) == 1

        latest = await repository.get_recipe(domain, "public_mentions")
        assert latest is not None
        assert latest.version == 3
    finally:
        await repository.unregister_worker(worker_a)
        await repository.unregister_worker(worker_b)
        with psycopg.connect(dsn) as conn:
            conn.execute("DELETE FROM argus.site_recipes WHERE domain=%s", (domain,))
            conn.execute("DELETE FROM argus.collections WHERE collection_id=%s", (collection_id,))
        await repository.close()
