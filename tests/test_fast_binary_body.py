import contextlib
import http.server
import socketserver
import threading

import pytest

from argus.config import Settings
from argus.crawler.fast.runtime import FastCrawlerRuntime
from argus.security.urls import UrlGuard


PDF_BODY = b"%PDF-1.7\nsynthetic-binary-body\n%%EOF"


class PdfHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Length", str(len(PDF_BODY)))
        self.end_headers()
        self.wfile.write(PDF_BODY)

    def log_message(self, format, *args):
        del format, args


@contextlib.contextmanager
def pdf_server():
    with socketserver.TCPServer(("127.0.0.1", 0), PdfHandler) as httpd:
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
        with pdf_server() as port:
            result = await runtime.fetch(f"http://127.0.0.1:{port}/report.pdf")
        assert result.content_type == "application/pdf"
        assert result.body == PDF_BODY
        assert result.final_url.endswith("/report.pdf")
    finally:
        await runtime.shutdown()
