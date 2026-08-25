from __future__ import annotations

from pathlib import Path

import pytest

from argus.contracts.models import (
    CollectionRecord,
    CollectionRequest,
    CollectionStatus,
    utcnow,
)
from argus.crawler.models import FetchResult
from argus.extraction.pdf import BoundedPdfExtractor
from argus.history.snapshots import SnapshotService
from argus.recipes.service import RecipeManager
from argus.sources.base import SourceTask
from argus.sources.historical_web import HistoricalTimelineWebAdapter
from argus.storage.lifecycle_sqlite import LifecycleAtomicSQLiteRepository


class UnusedRuntime:
    async def fetch(self, *args, **kwargs):
        raise AssertionError("runtime fetch is not used by extraction integration tests")


def request() -> CollectionRequest:
    return CollectionRequest(
        consumer="historical-web-test",
        analysis_id="historical-web-analysis",
        territory={"city": "Ижевск"},
        intents=["historical_context"],
        constraints={"max_depth": 2, "max_pages": 20},
    )


async def seed_collection(repository: LifecycleAtomicSQLiteRepository) -> None:
    now = utcnow()
    await repository.create_collection(
        CollectionRecord(
            collection_id="historical-web-collection",
            request=request(),
            status=CollectionStatus.RUNNING,
            created_at=now,
            updated_at=now,
        )
    )


def build_adapter(repository) -> HistoricalTimelineWebAdapter:
    return HistoricalTimelineWebAdapter(
        repository=repository,
        fast=UnusedRuntime(),
        browser=UnusedRuntime(),
        snapshots=SnapshotService(repository),
        recipes=RecipeManager(repository),
        agent=None,
        pdf_extractor=BoundedPdfExtractor(
            max_bytes=1_000_000,
            max_pages=10,
            max_text_chars=100_000,
            timeout_seconds=1.0,
            memory_mb=128,
        ),
    )


def task(timestamp: str) -> SourceTask:
    original = "https://example.com/project"
    return SourceTask(
        source_id="generic_web",
        goal="historical_context",
        url=f"https://web.archive.org/web/{timestamp}id_/{original}",
        metadata={
            "collection_id": "historical-web-collection",
            "archive_original_url": original,
            "archive_timestamp": timestamp,
            "discovery_provider": "wayback_cdx",
        },
    )


def fetched(timestamp: str, *, name: str, operator: str, brand: str) -> FetchResult:
    original = "https://example.com/project"
    url = f"https://web.archive.org/web/{timestamp}id_/{original}"
    html = f"""
    <html>
      <head><title>{name}</title></head>
      <body>
        <main><h1>{name}</h1><p>{operator} / {brand}</p></main>
        <script type="application/ld+json">
          {{
            "@context": "https://schema.org",
            "@type": "Organization",
            "@id": "https://example.com/org/project",
            "name": "{name}",
            "operator": "{operator}",
            "brand": "{brand}"
          }}
        </script>
        <a href="https://web.archive.org/web/{timestamp}id_/https://example.com/other">
          archived rewrite
        </a>
      </body>
    </html>
    """
    return FetchResult(
        url=url,
        final_url=url,
        status_code=200,
        content_type="text/html",
        text=html,
        title=name,
        links=[f"https://example.com/should-not-expand?capture={timestamp}"],
        runtime="fast",
    )


@pytest.mark.asyncio
async def test_second_capture_compares_only_against_committed_previous_capture(tmp_path: Path):
    repository = LifecycleAtomicSQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    await seed_collection(repository)
    adapter = build_adapter(repository)
    first_timestamp = "20240101000000"
    second_timestamp = "20250101000000"

    first = await adapter.extract(
        task(first_timestamp),
        fetched(first_timestamp, name="Old Name", operator="Operator A", brand="Brand A"),
        request(),
    )

    assert first.discovered_tasks == []
    first_versions = [
        item for item in first.observations if item.source_kind == "historical_page_version"
    ]
    assert len(first_versions) == 1
    assert first_versions[0].data["change_type"] == "first_observed_capture"
    raw_first = [
        item for item in first.observations if item.source_kind not in {
            "historical_page_version",
            "historical_entity_change",
        }
    ]
    assert raw_first
    assert all(item.provenance["archive"]["capture_timestamp"] == first_timestamp for item in raw_first)

    # Only committed observations are visible to the next worker/task. The first task's
    # derived rows are deliberately not needed to derive the next version.
    for item in first.observations:
        await repository.add_observation(item)
    for item in first.evidence:
        await repository.add_evidence(item, "historical-web-collection")

    second = await adapter.extract(
        task(second_timestamp),
        fetched(second_timestamp, name="New Name", operator="Operator B", brand="Brand B"),
        request(),
    )

    assert second.discovered_tasks == []
    versions = [
        item for item in second.observations if item.source_kind == "historical_page_version"
    ]
    assert len(versions) == 1
    assert versions[0].data["previous_capture_timestamp"] == first_timestamp
    assert versions[0].data["capture_timestamp"] == second_timestamp
    assert versions[0].data["change_type"] == "page_content_changed"

    changes = [
        item for item in second.observations if item.source_kind == "historical_entity_change"
    ]
    assert len(changes) == 1
    change = changes[0]
    assert change.data["change_type"] == "fields_changed"
    assert change.data["field_changes"]["name"] == {"from": "Old Name", "to": "New Name"}
    assert change.data["field_changes"]["operator"] == {
        "from": "Operator A",
        "to": "Operator B",
    }
    assert change.data["field_changes"]["brand"] == {"from": "Brand A", "to": "Brand B"}
    await repository.close()


@pytest.mark.asyncio
async def test_uncommitted_capture_does_not_become_previous_timeline_version(tmp_path: Path):
    repository = LifecycleAtomicSQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    await seed_collection(repository)
    adapter = build_adapter(repository)

    # Extract an earlier capture but intentionally do not persist its observations,
    # modeling a worker crash before atomic commit.
    await adapter.extract(
        task("20240101000000"),
        fetched("20240101000000", name="Uncommitted", operator="A", brand="A"),
        request(),
    )

    recovered = await adapter.extract(
        task("20250101000000"),
        fetched("20250101000000", name="Committed Later", operator="B", brand="B"),
        request(),
    )

    version = next(
        item for item in recovered.observations if item.source_kind == "historical_page_version"
    )
    assert version.data["previous_capture_timestamp"] is None
    assert version.data["change_type"] == "first_observed_capture"
    assert [
        item for item in recovered.observations if item.source_kind == "historical_entity_change"
    ] == []
    await repository.close()
