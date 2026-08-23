import hashlib
from pathlib import Path

import pytest

from argus.contracts.models import CollectionRequest
from argus.crawler.models import FetchResult
from argus.extraction.pdf import PdfExtraction
from argus.history.snapshots import SnapshotService
from argus.sources.base import SourceTask
from argus.sources.document_web import DocumentAwareGenericWebAdapter
from argus.storage.sqlite import SQLiteRepository


class FakePdfExtractor:
    def __init__(self, result: PdfExtraction) -> None:
        self.result = result
        self.calls: list[bytes] = []

    def extract(self, body: bytes) -> PdfExtraction:
        self.calls.append(body)
        return self.result


def request() -> CollectionRequest:
    return CollectionRequest(
        consumer="test",
        analysis_id="analysis-pdf",
        territory={"city": "Ижевск"},
        intents=["public_mentions"],
    )


def task() -> SourceTask:
    return SourceTask(
        source_id="generic_web",
        goal="public_mentions",
        url="https://example.com/report.pdf",
        metadata={
            "collection_id": "collection-pdf",
            "research_goals": ["public_mentions"],
        },
    )


def fetched(body: bytes) -> FetchResult:
    return FetchResult(
        url="https://example.com/report.pdf",
        final_url="https://example.com/report.pdf",
        status_code=200,
        content_type="application/pdf",
        text="binary replacement text must not become evidence",
        runtime="fast",
        body=body,
    )


@pytest.mark.asyncio
async def test_pdf_document_becomes_hash_backed_observation_and_text_evidence(tmp_path: Path):
    repository = SQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    extractor = FakePdfExtractor(
        PdfExtraction(
            text="Официальный факт из PDF",
            title="Публичный отчёт",
            page_count=12,
            pages_extracted=12,
            extractor_version="pypdf/test",
        )
    )
    adapter = DocumentAwareGenericWebAdapter(
        fast=object(),
        browser=object(),
        snapshots=SnapshotService(repository),
        pdf_extractor=extractor,
    )
    body = b"%PDF-1.7\nsynthetic-test-body"

    result = await adapter.extract(task(), fetched(body), request())

    assert extractor.calls == [body]
    assert result.partial is False
    assert result.errors == []
    assert len(result.observations) == 1
    assert len(result.evidence) == 1
    observation = result.observations[0]
    evidence = result.evidence[0]
    expected_hash = hashlib.sha256(body).hexdigest()
    assert observation.source_kind == "pdf_document"
    assert observation.title == "Публичный отчёт"
    assert observation.text == "Официальный факт из PDF"
    assert observation.content_hash == expected_hash
    assert observation.data["binary_sha256"] == expected_hash
    assert observation.data["page_count"] == 12
    assert observation.provenance["document"]["ocr_used"] is False
    assert evidence.type == "pdf_text"
    assert evidence.text == "Официальный факт из PDF"
    assert evidence.source.url == "https://example.com/report.pdf"

    snapshot = await repository.latest_snapshot("https://example.com/report.pdf")
    assert snapshot is not None
    assert observation.provenance["snapshot_id"] == snapshot.snapshot_id
    assert expected_hash in snapshot.content


@pytest.mark.asyncio
async def test_scanned_or_textless_pdf_is_explicit_partial_file_evidence(tmp_path: Path):
    repository = SQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    extractor = FakePdfExtractor(
        PdfExtraction(
            text="",
            page_count=3,
            pages_extracted=3,
            extractor_version="pypdf/test",
        )
    )
    adapter = DocumentAwareGenericWebAdapter(
        fast=object(),
        browser=object(),
        snapshots=SnapshotService(repository),
        pdf_extractor=extractor,
    )

    result = await adapter.extract(task(), fetched(b"%PDF-1.7\nscanned"), request())

    assert result.partial is True
    assert [error.code for error in result.errors] == ["PDF_TEXT_EMPTY"]
    assert result.observations[0].quality["text_extracted"] is False
    assert result.evidence[0].type == "pdf_file"
    assert "sha256=" in result.evidence[0].text
    assert result.evidence[0].metadata["ocr_used"] is False
