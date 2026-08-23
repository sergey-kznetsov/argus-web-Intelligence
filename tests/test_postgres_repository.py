import os
from uuid import uuid4

import pytest

from argus.contracts.models import (
    CollectionRecord,
    CollectionRequest,
    CollectionStatus,
    Evidence,
    EvidenceSource,
    Observation,
    Snapshot,
    utcnow,
)
from argus.recipes.models import RecipeStep, SiteRecipe
from argus.storage.postgres import PostgresRepository
from argus.storage.postgres_migrations import (
    EXPECTED_SCHEMA_VERSION,
    current_postgres_schema_version,
    run_postgres_migrations,
)


def postgres_dsn() -> str:
    value = os.getenv("ARGUS_TEST_POSTGRES_DSN", "").strip()
    if not value:
        pytest.skip("ARGUS_TEST_POSTGRES_DSN is not configured")
    return value


def collection(collection_id: str) -> CollectionRecord:
    created_at = utcnow()
    return CollectionRecord(
        collection_id=collection_id,
        request=CollectionRequest(
            consumer="postgres-test",
            analysis_id=f"analysis-{uuid4()}",
            territory={"city": "Ижевск"},
            intents=["public_mentions"],
        ),
        status=CollectionStatus.QUEUED,
        created_at=created_at,
        updated_at=created_at,
    )


@pytest.mark.asyncio
async def test_postgres_repository_full_contract():
    dsn = postgres_dsn()
    first_applied = await run_postgres_migrations(dsn)
    second_applied = await run_postgres_migrations(dsn)
    assert second_applied == []
    assert await current_postgres_schema_version(dsn) == EXPECTED_SCHEMA_VERSION
    assert set(first_applied).issubset(set(range(1, EXPECTED_SCHEMA_VERSION + 1)))

    repository = PostgresRepository(dsn, min_size=1, max_size=2, timeout_seconds=10)
    await repository.initialize()
    try:
        collection_id = f"test-{uuid4()}"
        record = collection(collection_id)
        request = record.request
        await repository.create_collection(record)
        loaded = await repository.get_collection(collection_id)
        assert loaded is not None
        assert loaded.request.consumer == "postgres-test"
        assert collection_id in {
            item.collection_id for item in await repository.list_recoverable_collections()
        }

        record.status = CollectionStatus.RUNNING
        record.stage = "integration-test"
        record.updated_at = utcnow()
        await repository.update_collection(record)
        loaded = await repository.get_collection(collection_id)
        assert loaded is not None
        assert loaded.status == CollectionStatus.RUNNING
        assert loaded.stage == "integration-test"

        observation = Observation(
            observation_id=f"obs-{uuid4()}",
            collection_id=collection_id,
            analysis_id=request.analysis_id,
            consumer=request.consumer,
            source="test",
            source_kind="document",
            url="https://example.com/page",
            entity_type="document",
            title="Example",
            text="Fact",
            content_hash="a" * 64,
        )
        await repository.add_observation(observation)
        observation.text = "Updated fact"
        await repository.add_observation(observation)
        observations = await repository.list_observations(collection_id)
        assert len(observations) == 1
        assert observations[0].text == "Updated fact"

        evidence = Evidence(
            evidence_id=f"evidence-{uuid4()}",
            observation_id=observation.observation_id,
            type="document",
            text="Updated fact",
            source=EvidenceSource(
                provider="test",
                url="https://example.com/page",
                collected_at=utcnow(),
                source_id="test",
            ),
        )
        await repository.add_evidence(evidence, collection_id)
        await repository.add_evidence(evidence, collection_id)
        evidence_items = await repository.list_evidence(collection_id)
        assert len(evidence_items) == 1
        assert evidence_items[0].evidence_id == evidence.evidence_id

        snapshot = Snapshot(
            snapshot_id=f"snapshot-{uuid4()}",
            source_id="test",
            source_url="https://example.com/page",
            content_hash="b" * 64,
            extractor_version="test-1",
            content_type="text/html",
            content="<html>Fact</html>",
        )
        await repository.add_snapshot(snapshot)
        latest = await repository.latest_snapshot(snapshot.source_url)
        assert latest is not None
        assert latest.snapshot_id == snapshot.snapshot_id

        recipe = SiteRecipe(
            recipe_id=f"recipe-{uuid4()}",
            domain="example.com",
            goal="public_mentions",
            version=1,
            steps=[RecipeStep(action="goto", value="https://example.com/page")],
        )
        await repository.save_recipe(recipe)
        saved_recipe = await repository.get_recipe("example.com", "public_mentions")
        assert saved_recipe is not None
        assert saved_recipe.recipe_id == recipe.recipe_id

        record.status = CollectionStatus.COMPLETED
        record.stage = "completed"
        record.updated_at = utcnow()
        await repository.update_collection(record)

        health = await repository.health()
        assert health["status"] == "ok"
        assert health["backend"] == "postgresql"
        assert health["schema_version"] == EXPECTED_SCHEMA_VERSION
    finally:
        await repository.close()


@pytest.mark.asyncio
async def test_postgres_worker_lease_is_exclusive_and_recoverable():
    dsn = postgres_dsn()
    await run_postgres_migrations(dsn)
    repository = PostgresRepository(dsn, min_size=1, max_size=3, timeout_seconds=10)
    await repository.initialize()
    worker_a = f"worker-a-{uuid4()}"
    worker_b = f"worker-b-{uuid4()}"
    collection_id = f"lease-{uuid4()}"
    try:
        await repository.register_worker(worker_a, metadata={"test": True})
        await repository.register_worker(worker_b, metadata={"test": True})
        assert await repository.active_worker_count(max_age_seconds=60) >= 2

        await repository.create_collection(collection(collection_id))
        assert await repository.claim_next_collection(worker_a, lease_seconds=30) == collection_id
        assert await repository.claim_next_collection(worker_b, lease_seconds=30) is None
        assert (
            await repository.renew_collection_lease(
                collection_id,
                worker_b,
                lease_seconds=30,
            )
            is False
        )
        assert (
            await repository.renew_collection_lease(
                collection_id,
                worker_a,
                lease_seconds=30,
            )
            is True
        )

        # Simulate a crashed worker without sleeping for the lease duration.
        async with repository._pool.connection() as conn:
            await conn.execute(
                """
                UPDATE argus.collection_leases
                SET lease_until=NOW() - INTERVAL '1 second'
                WHERE collection_id=%s
                """,
                (collection_id,),
            )

        assert await repository.claim_next_collection(worker_b, lease_seconds=30) == collection_id
        assert (
            await repository.renew_collection_lease(
                collection_id,
                worker_a,
                lease_seconds=30,
            )
            is False
        )
        await repository.release_collection_lease(collection_id, worker_b)

        record = await repository.get_collection(collection_id)
        assert record is not None
        record.status = CollectionStatus.CANCELLED
        record.stage = "test-cleanup"
        record.updated_at = utcnow()
        await repository.update_collection(record)

        await repository.unregister_worker(worker_a)
        await repository.unregister_worker(worker_b)
        assert await repository.active_worker_count(max_age_seconds=60) == 0
    finally:
        await repository.close()
