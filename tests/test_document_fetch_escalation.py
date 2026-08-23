import pytest

from argus.crawler.models import FetchResult
from argus.extraction.pdf import PdfExtraction
from argus.sources.base import SourceTask
from argus.sources.document_web import DocumentAwareGenericWebAdapter


class UnusedPdfExtractor:
    def extract(self, body: bytes) -> PdfExtraction:
        raise AssertionError("not used")


class FakeFast:
    def __init__(self, result: FetchResult) -> None:
        self.result = result
        self.calls = 0

    async def fetch(self, url: str) -> FetchResult:
        self.calls += 1
        return self.result


class FakeBrowser:
    def __init__(self, result: FetchResult) -> None:
        self.result = result
        self.calls = 0

    async def fetch(self, url: str, recipe=None) -> FetchResult:
        self.calls += 1
        return self.result


class DummySnapshots:
    pass


def task(url: str) -> SourceTask:
    return SourceTask(source_id="generic_web", goal="public_mentions", url=url)


@pytest.mark.asyncio
async def test_json_response_with_html_marker_stays_in_fast_runtime():
    url = "https://example.com/data.json"
    fast_result = FetchResult(
        url=url,
        final_url=url,
        status_code=200,
        content_type="application/json",
        text='{"message":"enable javascript"}',
        runtime="fast",
        body=b'{"message":"enable javascript"}',
    )
    browser_result = FetchResult(
        url=url,
        final_url=url,
        status_code=200,
        content_type="text/html",
        text="browser",
        runtime="browser",
    )
    fast = FakeFast(fast_result)
    browser = FakeBrowser(browser_result)
    adapter = DocumentAwareGenericWebAdapter(
        fast=fast,
        browser=browser,
        snapshots=DummySnapshots(),
        pdf_extractor=UnusedPdfExtractor(),
    )

    result = await adapter.fetch(task(url))

    assert result is fast_result
    assert fast.calls == 1
    assert browser.calls == 0


@pytest.mark.asyncio
async def test_html_shell_still_escalates_to_browser():
    url = "https://example.com/app"
    fast_result = FetchResult(
        url=url,
        final_url=url,
        status_code=200,
        content_type="text/html",
        text='<html><div id="root"></div></html>',
        runtime="fast",
        body=b'<html><div id="root"></div></html>',
    )
    browser_result = FetchResult(
        url=url,
        final_url=url,
        status_code=200,
        content_type="text/html",
        text="rendered",
        runtime="browser",
    )
    fast = FakeFast(fast_result)
    browser = FakeBrowser(browser_result)
    adapter = DocumentAwareGenericWebAdapter(
        fast=fast,
        browser=browser,
        snapshots=DummySnapshots(),
        pdf_extractor=UnusedPdfExtractor(),
    )

    result = await adapter.fetch(task(url))

    assert result is browser_result
    assert fast.calls == 1
    assert browser.calls == 1
