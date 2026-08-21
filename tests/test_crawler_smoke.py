import contextlib
import http.server
import socketserver
import threading

import pytest

from argus.config import Settings
from argus.crawler.browser.runtime import BrowserCrawlerRuntime
from argus.crawler.fast.runtime import FastCrawlerRuntime
from argus.security.urls import UrlGuard


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = b"<html><head><title>Smoke</title></head><body><div id='app'></div><script>document.getElementById('app').innerText='Rendered';</script></body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


@contextlib.contextmanager
def server():
    with socketserver.TCPServer(("127.0.0.1", 0), Handler) as httpd:
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            yield httpd.server_address[1]
        finally:
            httpd.shutdown()
            thread.join()


@pytest.mark.asyncio
async def test_fast_runtime_real_crawlee():
    settings = Settings(allow_internal_targets=["127.0.0.1"])
    guard = UrlGuard.from_strings(settings.allow_internal_targets)
    with server() as port:
        result = await FastCrawlerRuntime(settings, guard).fetch(f"http://127.0.0.1:{port}/")
        assert result.status_code == 200
        assert result.title == "Smoke"


@pytest.mark.asyncio
async def test_browser_runtime_executes_javascript():
    settings = Settings(allow_internal_targets=["127.0.0.1"])
    guard = UrlGuard.from_strings(settings.allow_internal_targets)
    with server() as port:
        result = await BrowserCrawlerRuntime(settings, guard).fetch(f"http://127.0.0.1:{port}/")
        assert "Rendered" in result.text
