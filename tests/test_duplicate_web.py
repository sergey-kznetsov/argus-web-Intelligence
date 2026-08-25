from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.contracts.models import (
    CollectionRecord,
    CollectionRequest,
    CollectionStatus,
    utcnow,
)
from argus.extraction.pdf import BoundedPdfExtractor
from argus.history.snapshots import SnapshotService
from argus.sources.base import SourceTask
from argus.sources.duplicate_web import DuplicateAwareWebAdapter
from argus.storage.atomic_sqlite import AtomicSQLiteRepository


class FastStub:
    pass


class BrowserStub:
    pass


def build_adapter(repository: AtomicSQLiteRepository) -> DuplicateAwareWebAdapter:
    return DuplicateAwareWebAdapter(
        repository=repository,
        fast=FastStub(),
        browser=BrowserStub(),
        snapshots=SnapshotService(repository),
        pdf_extractor=BoundedPdfExtractor(
            max_bytes=1_000_000,
            max_pages=10,
            max_text_chars=100_000,
            timeout_seconds=1.0,
            memory_mb=128,
        ),
    )


def request() -> CollectionRequest:
    return CollectionRequest(
        consumer="duplicate-test",
        analysis_id="analysis-duplicate",
        territory={"city": "Ижевск"},
        intents=["public_mentions", "historical_context"],
        constraints={"max_depth": 2, "max_pages": 20},
    )


def task(url: str, collection_id: str) -> SourceTask:
    return SourceTask(
        source_id="generic_web",
        goal="public_mentions",
        url=url,
        metadata={"collection_id": collection_id, "research_goals": ["public_mentions"]},
    )


def fetched(url: str, html: str, links: list[str]):
    return SimpleNamespace(
        blocked=False,
        final_url=url,
        text=html,
        content_type="text/html; charset=utf-8",
        runtime="fast",
        status_code=200,
        metadata={},
        title="Example",
        links=links,
        body=html.encode("utf-8"),
    )


async def create_collection(repository: AtomicSQLiteRepository, collection_id: str) -> None:
    timestamp = utcnow()
    await repository.create_collection(
        CollectionRecord(
            collection_id=collection_id,
            request=request(),
            status=CollectionStatus.RUNNING,
            created_at=timestamp,
            updated_at=timestamp,
        )
    )


@pytest.mark.asyncio
async def test_committed_duplicate_survives_adapter_restart_and_preserves_evidence(tmp_path: Path):
    repository = AtomicSQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    collection_id = "duplicate-restart"
    await create_collection(repository, collection_id)
    body = "<html><body><main>" + ("Same factual article text. " * 30) + "</main></body></html>"

    first_adapter = build_adapter(repository)
    first = await first_adapter.extract(
        task("https://example.com/original", collection_id),
        fetched(
            "https://example.com/original",
            body,
            ["https://example.com/original-child"],
        ),
        request(),
    )
    first_primary = next(item for item in first.observations if item.source_kind == "web_page")
    await repository.add_observation(first_primary)

    # A fresh adapter instance models worker/process restart. Only committed storage
    # carries duplicate identity across the restart.
    restarted_adapter = build_adapter(repository)
    second_task = task("https://mirror.example.com/copy", collection_id)
    second = await restarted_adapter.extract(
        second_task,
        fetched(
            "https://mirror.example.com/copy",
            body,
            ["https://mirror.example.com/should-not-expand"],
        ),
        request(),
    )

    second_primary = next(item for item in second.observations if item.source_kind == "web_page")
    primary_evidence = next(
        item for item in second.evidence if item.observation_id == second_primary.observation_id
    )

    assert second.discovered_tasks == []
    assert second_primary.observation_id != first_primary.observation_id
    assert second_primary.quality["duplicate_content"] is True
    assert second_primary.quality["duplicate_of"] == first_primary.observation_id
    assert second_primary.data["duplicate_navigation_suppressed"] is True
    assert second_primary.provenance["duplicate_content"]["url"] == first_primary.url
    assert primary_evidence.metadata["duplicate_content"]["observation_id"] == first_primary.observation_id
    assert second_task.metadata["duplicate_content"] is True
    assert second_task.metadata["duplicate_of_observation_id"] == first_primary.observation_id
    assert second.evidence
    await repository.close()


@pytest.mark.asyncio
async def test_short_template_text_does_not_suppress_navigation(tmp_path: Path):
    repository = AtomicSQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    collection_id = "duplicate-short"
    await create_collection(repository, collection_id)
    body = "<html><body><main>Short template</main></body></html>"

    web = build_adapter(repository)
    first = await web.extract(
        task("https://example.com/one", collection_id),
        fetched("https://example.com/one", body, []),
        request(),
    )
    first_primary = next(item for item in first.observations if item.source_kind == "web_page")
    await repository.add_observation(first_primary)

    second = await web.extract(
        task("https://example.com/two", collection_id),
        fetched(
            "https://example.com/two",
            body,
            ["https://example.com/two/child"],
        ),
        request(),
    )
    second_primary = next(item for item in second.observations if item.source_kind == "web_page")

    assert "duplicate_content" not in second_primary.quality
    assert any(item.url == "https://example.com/two/child" for item in second.discovered_tasks)
    await repository.close()


@pytest.mark.asyncio
async def test_duplicate_page_does_not_retag_embedded_structured_fact(tmp_path: Path):
    repository = AtomicSQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    collection_id = "duplicate-structured"
    await create_collection(repository, collection_id)
    visible = "Same long public article body. " * 30
    first_html = f"""<html><body><main>{visible}</main>
    <script type="application/ld+json">{{"@context":"https://schema.org","@type":"Article","headline":"One"}}</script>
    </body></html>"""
    second_html = f"""<html><body><main>{visible}</main>
    <script type="application/ld+json">{{"@context":"https://schema.org","@type":"Article","headline":"Two"}}</script>
    </body></html>"""

    web = build_adapter(repository)
    first = await web.extract(
        task("https://example.com/a", collection_id),
        fetched("https://example.com/a", first_html, []),
        request(),
    )
    first_primary = next(item for item in first.observations if item.source_kind == "web_page")
    await repository.add_observation(first_primary)

    second = await web.extract(
        task("https://example.com/b", collection_id),
        fetched("https://example.com/b", second_html, []),
        request(),
    )
    primary = next(item for item in second.observations if item.source_kind == "web_page")
    structured = next(item for item in second.observations if item.source_kind == "json_ld")

    assert primary.quality["duplicate_content"] is True
    assert "duplicate_content" not in structured.quality
    assert "duplicate_content" not in structured.provenance
    assert structured.title == "Two"
    await repository.close()
