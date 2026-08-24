import gzip
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.contracts.models import CollectionRequest
from argus.extraction.pdf import BoundedPdfExtractor
from argus.extraction.structured_data import BoundedStructuredDataExtractor
from argus.history.snapshots import SnapshotService
from argus.sources.base import SourceTask
from argus.sources.compressed_web import CompressedOfficeAwareGenericWebAdapter
from argus.storage.sqlite import SQLiteRepository


class FastStub:
    def __init__(self, result=None) -> None:
        self.result = result
        self.calls = 0

    async def fetch(self, url):
        del url
        self.calls += 1
        return self.result


class BrowserStub:
    def __init__(self) -> None:
        self.calls = 0

    async def fetch(self, url, recipe=None):
        del url, recipe
        self.calls += 1
        raise AssertionError("gzip structured document must not escalate to browser")


def pdf_extractor() -> BoundedPdfExtractor:
    return BoundedPdfExtractor(
        max_bytes=1024,
        max_pages=1,
        max_text_chars=1000,
        timeout_seconds=1,
        memory_mb=128,
    )


def request() -> CollectionRequest:
    return CollectionRequest(
        consumer="gzip-test",
        analysis_id="analysis-gzip-1",
        territory={"city": "Ижевск"},
        intents=["public_mentions"],
    )


def adapter(repository: SQLiteRepository, *, fast=None, browser=None):
    return CompressedOfficeAwareGenericWebAdapter(
        fast=fast or FastStub(),
        browser=browser or BrowserStub(),
        snapshots=SnapshotService(repository),
        pdf_extractor=pdf_extractor(),
        structured_data_extractor=BoundedStructuredDataExtractor(
            max_bytes=4096,
            max_records=100,
            max_columns=20,
            max_cell_chars=1000,
        ),
    )


def fetched(url: str, body: bytes, content_type: str = "application/gzip"):
    return SimpleNamespace(
        blocked=False,
        final_url=url,
        text="",
        content_type=content_type,
        runtime="fast",
        status_code=200,
        metadata={},
        title=None,
        links=[],
        body=body,
    )


@pytest.mark.asyncio
async def test_csv_gzip_is_parsed_with_compressed_source_identity(tmp_path: Path):
    repository = SQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    value = adapter(repository)
    csv_body = "name,value\nШкола,1\n".encode("utf-8")
    compressed = gzip.compress(csv_body)
    source_url = "https://example.com/open/data.csv.gz?download=1"
    task = SourceTask(
        source_id="generic_web",
        goal="public_mentions",
        url=source_url,
        metadata={
            "collection_id": "collection-gzip-1",
            "research_goals": ["public_mentions"],
        },
    )

    result = await value.extract(task, fetched(source_url, compressed), request())

    assert result.partial is False
    assert result.errors == []
    assert len(result.observations) == 1
    observation = result.observations[0]
    evidence = result.evidence[0]
    compressed_sha = hashlib.sha256(compressed).hexdigest()
    uncompressed_sha = hashlib.sha256(csv_body).hexdigest()

    assert observation.source_kind == "structured_data"
    assert observation.data["document_type"] == "csv"
    assert observation.data["payload"]["records"] == [{"name": "Школа", "value": "1"}]
    assert observation.content_hash == compressed_sha
    compression = observation.data["compression"]
    assert compression["format"] == "gzip"
    assert compression["compressed_sha256"] == compressed_sha
    assert compression["uncompressed_sha256"] == uncompressed_sha
    assert compression["compressed_bytes"] == len(compressed)
    assert compression["uncompressed_bytes"] == len(csv_body)
    assert compression["inner_url"] == "https://example.com/open/data.csv?download=1"
    assert observation.provenance["document"]["compression"] == compression
    assert evidence.metadata["compression"] == compression
    assert evidence.source.url == source_url

    snapshot = await repository.latest_snapshot(source_url)
    assert snapshot is not None
    assert compressed_sha in snapshot.content
    await repository.close()


@pytest.mark.asyncio
async def test_invalid_gzip_remains_evidence_backed_partial_result(tmp_path: Path):
    repository = SQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    value = adapter(repository)
    source_url = "https://example.com/open/data.json.gz"
    body = b"not-a-gzip-stream"
    task = SourceTask(
        source_id="generic_web",
        goal="public_mentions",
        url=source_url,
        metadata={"collection_id": "collection-gzip-2"},
    )

    result = await value.extract(task, fetched(source_url, body), request())

    assert result.partial is True
    assert [error.code for error in result.errors] == ["GZIP_INVALID"]
    assert len(result.observations) == 1
    observation = result.observations[0]
    assert observation.content_hash == hashlib.sha256(body).hexdigest()
    assert observation.data["compression"]["error_code"] == "GZIP_INVALID"
    assert observation.quality["parsed"] is False
    assert result.evidence[0].type == "structured_file"
    await repository.close()


@pytest.mark.asyncio
async def test_gzip_structured_response_stays_on_fast_path(tmp_path: Path):
    repository = SQLiteRepository(tmp_path / "argus.sqlite")
    await repository.initialize()
    csv_body = b"name,value\nA,1\n"
    compressed = gzip.compress(csv_body)
    source_url = "https://example.com/open/data.csv.gz"
    fast_result = fetched(source_url, compressed)
    fast = FastStub(fast_result)
    browser = BrowserStub()
    value = adapter(repository, fast=fast, browser=browser)
    task = SourceTask(
        source_id="generic_web",
        goal="public_mentions",
        url=source_url,
    )

    returned = await value.fetch(task)

    assert returned is fast_result
    assert fast.calls == 1
    assert browser.calls == 0
    await repository.close()


def test_html_response_for_gzip_url_is_not_misclassified_as_compressed_document(tmp_path: Path):
    repository = SQLiteRepository(tmp_path / "argus.sqlite")
    value = adapter(repository)
    response = fetched(
        "https://example.com/data.csv.gz",
        b"<html>not found</html>",
        content_type="text/html",
    )

    assert value._gzip_inner_url(response) is None
