import hashlib
from pathlib import Path

import pytest

from argus.contracts.models import CollectionRequest
from argus.crawler.models import FetchResult
from argus.extraction.pdf import PdfExtraction
from argus.extraction.structured_data import BoundedStructuredDataExtractor
from argus.history.snapshots import SnapshotService
from argus.sources.base import SourceTask
from argus.sources.document_web import DocumentAwareGenericWebAdapter
from argus.storage.sqlite import SQLiteRepository


class UnusedPdfExtractor:
    def extract(self, body: bytes) -> PdfExtraction:
        raise AssertionError("PDF extractor must not be used for structured data")


def request() -> CollectionRequest:
    return CollectionRequest(
        consumer="test",
        analysis_id="analysis-structured",
        territory={"city": "Ижевск"},
        intents=["public_mentions"],
    )


def task(url: str) -> SourceTask:
    return SourceTask(
        source_id="generic_web",
        goal="public_mentions",
        url=url,
        metadata={
            "collection_id": "collection-structured",
            "research_goals": ["public_mentions"],
        },
    )


def fetched(url: str, body: bytes, content_type: str) -> FetchResult:
    return FetchResult(
        url=url,
        final_url=url,
        status_code=200,
        content_type=content_type,
        text=body.decode("utf-8", errors="replace"),
        runtime="fast",
        body=body,
    )


@pytest.mark.asyncio
async def test_csv_document_becomes_structured_observation_and_evidence(tmp_path: Path):
    repository = SQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    adapter = DocumentAwareGenericWebAdapter(
        fast=object(),
        browser=object(),
        snapshots=SnapshotService(repository),
        pdf_extractor=UnusedPdfExtractor(),
        structured_data_extractor=BoundedStructuredDataExtractor(
            max_bytes=1024 * 1024,
            max_records=10,
            max_columns=10,
            max_cell_chars=100,
            max_json_depth=10,
            max_json_nodes=100,
        ),
    )
    url = "https://example.com/public.csv"
    body = "name;value\nДом;12\nШкола;3\n".encode()

    result = await adapter.extract(task(url), fetched(url, body, "text/csv"), request())

    assert result.partial is False
    assert result.errors == []
    assert len(result.observations) == 1
    assert len(result.evidence) == 1
    observation = result.observations[0]
    evidence = result.evidence[0]
    expected_hash = hashlib.sha256(body).hexdigest()
    assert observation.source_kind == "structured_data"
    assert observation.entity_type == "dataset"
    assert observation.content_hash == expected_hash
    assert observation.data["document_type"] == "csv"
    assert observation.data["binary_sha256"] == expected_hash
    assert observation.data["payload"]["records"][0] == {"name": "Дом", "value": "12"}
    assert observation.provenance["document"]["parser_network_access"] is False
    assert evidence.type == "structured_data"
    assert '"Школа"' in evidence.text

    snapshot = await repository.latest_snapshot(url)
    assert snapshot is not None
    assert observation.provenance["snapshot_id"] == snapshot.snapshot_id
    assert expected_hash in snapshot.content


@pytest.mark.asyncio
async def test_invalid_json_is_partial_but_keeps_file_evidence(tmp_path: Path):
    repository = SQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    adapter = DocumentAwareGenericWebAdapter(
        fast=object(),
        browser=object(),
        snapshots=SnapshotService(repository),
        pdf_extractor=UnusedPdfExtractor(),
    )
    url = "https://example.com/data.json"
    body = b'{"broken":'

    result = await adapter.extract(
        task(url),
        fetched(url, body, "application/json"),
        request(),
    )

    assert result.partial is True
    assert [error.code for error in result.errors] == ["STRUCTURED_DATA_PARSE_ERROR"]
    assert result.observations[0].quality["parsed"] is False
    assert result.evidence[0].type == "structured_file"
    assert "sha256=" in result.evidence[0].text
