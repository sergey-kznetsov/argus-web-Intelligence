import hashlib
from pathlib import Path

import pytest

from argus.contracts.models import CollectionRequest
from argus.crawler.models import FetchResult
from argus.extraction.pdf import PdfExtraction
from argus.history.snapshots import SnapshotService
from argus.sources.base import SourceTask
from argus.sources.office_web import OfficeAwareGenericWebAdapter
from argus.storage.sqlite import SQLiteRepository


class UnusedPdfExtractor:
    def extract(self, body: bytes) -> PdfExtraction:
        raise AssertionError("PDF extractor must not be used for Office documents")


def request() -> CollectionRequest:
    return CollectionRequest(
        consumer="test",
        analysis_id="analysis-office",
        territory={"city": "Ижевск"},
        intents=["public_mentions"],
    )


def task(url: str) -> SourceTask:
    return SourceTask(
        source_id="generic_web",
        goal="public_mentions",
        url=url,
        metadata={
            "collection_id": "collection-office",
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
async def test_binary_xls_is_not_parsed_as_csv(tmp_path: Path):
    repository = SQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    adapter = OfficeAwareGenericWebAdapter(
        fast=object(),
        browser=object(),
        snapshots=SnapshotService(repository),
        pdf_extractor=UnusedPdfExtractor(),
    )
    url = "https://example.com/report.xls"
    body = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1binary,not,csv\x00\xff"

    result = await adapter.extract(
        task(url),
        fetched(url, body, "application/vnd.ms-excel"),
        request(),
    )

    assert result.partial is True
    assert [error.code for error in result.errors] == ["OFFICE_FORMAT_NOT_PARSED"]
    observation = result.observations[0]
    evidence = result.evidence[0]
    expected_hash = hashlib.sha256(body).hexdigest()
    assert observation.source_kind == "office_document_file"
    assert observation.data["document_type"] == "xls"
    assert observation.data["parsed"] is False
    assert observation.content_hash == expected_hash
    assert "payload" not in observation.data
    assert evidence.type == "office_file"
    assert evidence.metadata["document_type"] == "xls"
    assert expected_hash in evidence.text

    snapshot = await repository.latest_snapshot(url)
    assert snapshot is not None
    assert expected_hash in snapshot.content


@pytest.mark.asyncio
async def test_csv_extension_overrides_ambiguous_excel_mime(tmp_path: Path):
    repository = SQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    adapter = OfficeAwareGenericWebAdapter(
        fast=object(),
        browser=object(),
        snapshots=SnapshotService(repository),
        pdf_extractor=UnusedPdfExtractor(),
    )
    url = "https://example.com/export.csv"
    body = "name;value\nШкола;3\n".encode("utf-8")

    result = await adapter.extract(
        task(url),
        fetched(url, body, "application/vnd.ms-excel"),
        request(),
    )

    assert result.partial is False
    assert result.errors == []
    observation = result.observations[0]
    assert observation.source_kind == "structured_data"
    assert observation.data["document_type"] == "csv"
    assert observation.data["payload"]["records"][0] == {"name": "Школа", "value": "3"}


def test_office_classifier_prefers_explicit_file_suffix():
    xlsx = fetched(
        "https://example.com/report.xlsx",
        b"PK\x03\x04",
        "application/octet-stream",
    )
    assert OfficeAwareGenericWebAdapter._office_type(xlsx) == "xlsx"

    csv = fetched(
        "https://example.com/report.csv",
        b"a,b\n1,2\n",
        "application/vnd.ms-excel",
    )
    assert OfficeAwareGenericWebAdapter._office_type(csv) is None
