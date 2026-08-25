from pathlib import Path
from types import SimpleNamespace

from argus.contracts.models import CollectionRequest
from argus.extraction.pdf import BoundedPdfExtractor
from argus.history.snapshots import SnapshotService
from argus.sources.base import SourceTask
from argus.sources.canonical_web import CanonicalLinkWebAdapter
from argus.storage.sqlite import SQLiteRepository


class FastStub:
    pass


class BrowserStub:
    pass


def adapter(repository: SQLiteRepository) -> CanonicalLinkWebAdapter:
    return CanonicalLinkWebAdapter(
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


def request(**constraints) -> CollectionRequest:
    return CollectionRequest(
        consumer="canonical-navigation-test",
        analysis_id="canonical-navigation-1",
        territory={"city": "Ижевск"},
        intents=["public_mentions"],
        constraints=constraints,
    )


def source_task() -> SourceTask:
    return SourceTask(
        source_id="generic_web",
        goal="public_mentions",
        url="https://example.com/start",
        metadata={"collection_id": "collection-navigation-1"},
    )


def test_seed_urls_use_same_canonical_identity(tmp_path: Path):
    repository = SQLiteRepository(tmp_path / "argus.sqlite")
    web = adapter(repository)
    plan = request(
        seed_urls=[
            "https://Example.com:443/a?utm_source=x&id=1#one",
            "https://example.com/a?id=1#two",
        ]
    )

    import asyncio

    tasks = asyncio.run(web.discover(plan))

    assert len(tasks) == 1
    assert tasks[0].url == "https://example.com/a?id=1"
    # CollectionRequest validates seed URLs as HttpUrl, so Pydantic normalizes the
    # authority before discovery. The pre-canonical navigation URL still preserves
    # tracking parameters and fragments for provenance/debugging.
    assert tasks[0].metadata["navigation_original_url"] == (
        "https://example.com/a?utm_source=x&id=1#one"
    )
    assert tasks[0].metadata["navigation_identity_version"] == "discovery-url-identity/1"


def test_in_page_links_are_canonicalized_before_task_dedupe(tmp_path: Path):
    repository = SQLiteRepository(tmp_path / "argus.sqlite")
    web = adapter(repository)
    fetched = SimpleNamespace(
        final_url="https://example.com/start",
        content_type="text/html",
        text="<html></html>",
        links=[
            "https://example.com/article?id=1&utm_medium=a#top",
            "https://Example.com:443/article?id=1#comments",
            "https://example.com/article?id=2",
        ],
    )

    tasks = web._discovered_tasks(
        source_task(),
        fetched,
        request(max_depth=2, max_pages=10),
        "collection-navigation-1",
    )

    generic = [item for item in tasks if item.source_id == "generic_web"]
    assert [item.url for item in generic] == [
        "https://example.com/article?id=1",
        "https://example.com/article?id=2",
    ]
    assert generic[0].metadata["navigation_canonical_url"] == "https://example.com/article?id=1"


def test_explicit_provider_task_keys_are_not_rewritten(tmp_path: Path):
    repository = SQLiteRepository(tmp_path / "argus.sqlite")
    web = adapter(repository)
    task = SourceTask(
        source_id="site_discovery",
        goal="public_mentions",
        url="https://Example.com:443/robots.txt#ignored-by-provider-contract",
        task_key="site_discovery:robots:https://Example.com:443",
    )

    normalized = web._canonicalize_navigation_tasks([task])

    assert normalized == [task]
    assert normalized[0].task_key == "site_discovery:robots:https://Example.com:443"
