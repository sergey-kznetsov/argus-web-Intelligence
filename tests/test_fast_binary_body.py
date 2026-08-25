import contextlib
import http.server
import socketserver
import threading

import pytest

from argus.config import Settings
from argus.crawler.fast.runtime import FastCrawlerRuntime
from argus.security.urls import UrlGuard


PDF_BODY = b"%PDF-1.7\naccess denied is source text, not an HTTP block\n%%EOF"


class PdfHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Length", str(len(PDF_BODY)))
        self.end_headers()
        self.wfile.write(PDF_BODY)

    def log_message(self, format, *args):
        del format, args


class UnknownLengthHandler(http.server.BaseHTTPRequestHandler):
    body = b"x" * 4096

    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, format, *args):
        del format, args


class FakeStreamingResponse:
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.read_count = 0

    async def read_stream(self):
        for chunk in self.chunks:
            self.read_count += 1
            yield chunk


@contextlib.contextmanager
def server(handler):
    with socketserver.TCPServer(("127.0.0.1", 0), handler) as httpd:
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            yield httpd.server_address[1]
        finally:
            httpd.shutdown()
            thread.join()


@pytest.mark.asyncio
async def test_fast_runtime_preserves_bounded_raw_pdf_body():
    settings = Settings(
        allow_internal_targets=["127.0.0.1"],
        max_response_bytes=1024,
    )
    runtime = FastCrawlerRuntime(
        settings,
        UrlGuard.from_strings(settings.allow_internal_targets),
    )
    try:
        with server(PdfHandler) as port:
            result = await runtime.fetch(f"http://127.0.0.1:{port}/report.pdf")
        assert result.content_type == "application/pdf"
        assert result.body == PDF_BODY
        assert result.blocked is False
        assert result.final_url.endswith("/report.pdf")
    finally:
        await runtime.shutdown()


@pytest.mark.asyncio
async def test_bounded_stream_stops_before_consuming_later_chunks():
    response = FakeStreamingResponse([b"a" * 8, b"b" * 8, b"c" * 8])

    with pytest.raises(ValueError, match="response body exceeds configured limit"):
        await FastCrawlerRuntime._read_bounded_stream(response, max_bytes=12)

    assert response.read_count == 2


@pytest.mark.asyncio
async def test_fast_runtime_rejects_unknown_length_body_over_limit():
    settings = Settings(
        allow_internal_targets=["127.0.0.1"],
        max_response_bytes=1024,
        max_concurrency=1,
    )
    runtime = FastCrawlerRuntime(
        settings,
        UrlGuard.from_strings(settings.allow_internal_targets),
    )
    try:
        with server(UnknownLengthHandler) as port:
            with pytest.raises(ValueError, match="response body exceeds configured limit"):
                await runtime.fetch(f"http://127.0.0.1:{port}/large.bin")
    finally:
        await runtime.shutdown()
