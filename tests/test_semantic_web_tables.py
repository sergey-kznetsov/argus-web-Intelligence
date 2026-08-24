from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.contracts.models import CollectionRequest
from argus.history.snapshots import SnapshotService
from argus.sources.base import SourceTask
from argus.sources.semantic_web import SemanticWebAdapter
from argus.storage.sqlite import SQLiteRepository


class FastStub:
    pass


class BrowserStub:
    pass


def request() -> CollectionRequest:
    return CollectionRequest(
        consumer="semantic-table-test",
        analysis_id="analysis-table-1",
        territory={"city": "Ижевск"},
        intents=["public_mentions"],
    )


def fetched(html: str):
    return SimpleNamespace(
        blocked=False,
        final_url="https://example.com/data",
        text=html,
        content_type="text/html; charset=utf-8",
        runtime="fast",
        status_code=200,
        metadata={},
        title="Public data",
        links=[],
        body=html.encode("utf-8"),
    )


@pytest.mark.asyncio
async def test_semantic_table_reuses_page_snapshot_and_adds_dataset_evidence(tmp_path: Path):
    repository = SQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    adapter = SemanticWebAdapter(
        fast=FastStub(),
        browser=BrowserStub(),
        snapshots=SnapshotService(repository),
    )
    task = SourceTask(
        source_id="generic_web",
        goal="public_mentions",
        url="https://example.com/data",
        metadata={
            "collection_id": "collection-table-1",
            "research_goals": ["public_mentions"],
        },
    )
    html = """
    <html><body>
      <h1>Public data</h1>
      <table>
        <caption>Объекты</caption>
        <tr><th>Название</th><th>Адрес</th></tr>
        <tr><td>Школа 1</td><td>ул. Ленина, 1</td></tr>
        <tr><td>Школа 2</td><td>ул. Мира, 2</td></tr>
      </table>
    </body></html>
    """

    result = await adapter.extract(task, fetched(html), request())

    page = next(item for item in result.observations if item.source_kind == "web_page")
    table = next(item for item in result.observations if item.source_kind == "html_table")
    table_evidence = next(item for item in result.evidence if item.type == "html_table")

    assert page.data["html_table_summary"] == {
        "tables_seen": 1,
        "tables_extracted": 1,
        "layout_skipped": 0,
        "complex_skipped": 0,
        "empty_skipped": 0,
        "truncated": False,
        "extractor": "html-table/1",
    }
    assert table.entity_type == "dataset"
    assert table.title == "Объекты"
    assert table.url == "https://example.com/data"
    assert table.data["headers"] == ["Название", "Адрес"]
    assert table.data["rows"] == [
        ["Школа 1", "ул. Ленина, 1"],
        ["Школа 2", "ул. Мира, 2"],
    ]
    assert table.quality["lossless"] is True
    assert table.provenance["snapshot_id"] == page.provenance["snapshot_id"]
    assert table_evidence.observation_id == table.observation_id
    assert table_evidence.source.url == "https://example.com/data"
    assert table_evidence.metadata["snapshot_id"] == page.provenance["snapshot_id"]
    assert table_evidence.metadata["evidence_excerpt_truncated"] is False

    snapshot = await repository.latest_snapshot("https://example.com/data")
    assert snapshot is not None
    assert snapshot.snapshot_id == page.provenance["snapshot_id"]
    await repository.close()


@pytest.mark.asyncio
async def test_complex_table_is_reported_but_not_normalized_as_fact(tmp_path: Path):
    repository = SQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    adapter = SemanticWebAdapter(
        fast=FastStub(),
        browser=BrowserStub(),
        snapshots=SnapshotService(repository),
    )
    task = SourceTask(
        source_id="generic_web",
        goal="public_mentions",
        url="https://example.com/data",
        metadata={"collection_id": "collection-table-2"},
    )
    html = """
    <table>
      <tr><th colspan="2">Сводка</th></tr>
      <tr><td>A</td><td>B</td></tr>
    </table>
    """

    result = await adapter.extract(task, fetched(html), request())

    assert not any(item.source_kind == "html_table" for item in result.observations)
    page = next(item for item in result.observations if item.source_kind == "web_page")
    assert page.data["html_table_summary"]["tables_seen"] == 1
    assert page.data["html_table_summary"]["complex_skipped"] == 1
    assert page.data["html_table_summary"]["tables_extracted"] == 0
    await repository.close()


@pytest.mark.asyncio
async def test_semantic_table_health_capability_is_exposed(tmp_path: Path):
    repository = SQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    adapter = SemanticWebAdapter(
        fast=FastStub(),
        browser=BrowserStub(),
        snapshots=SnapshotService(repository),
    )

    health = await adapter.health()

    assert health["semantic_html_table_extraction"] is True
    await repository.close()
