import contextlib
import http.server
import socketserver
import threading

import pytest

from argus.config import Settings
from argus.crawler.browser.runtime import BrowserCrawlerRuntime
from argus.crawler.fast.runtime import FastCrawlerRuntime
from argus.security.urls import UnsafeUrlError, UrlGuard


class Handler(http.server.BaseHTTPRequestHandler):
    secret_hits = 0

    def do_GET(self):  # noqa: N802
        if self.path == "/redirect":
            port = self.server.server_address[1]
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{port}/secret")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if self.path == "/secret":
            type(self).secret_hits += 1

        body = (
            b"<html><head><title>Smoke</title></head><body>"
            b"<div id='app'></div>"
            b"<script>document.getElementById('app').innerText='Rendered';</script>"
            b"</body></html>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


@contextlib.contextmanager
def server():
    Handler.secret_hits = 0
    with socketserver.TCPServer(("127.0.0.1", 0), Handler) as httpd:
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            yield httpd.server_address[1]
        finally:
            httpd.shutdown()
            thread.join()


@pytest.mark.asyncio
async def test_fast_runtime_real_crawlee_and_reuses_runtime():
    settings = Settings(allow_internal_targets=["127.0.0.1"])
    guard = UrlGuard.from_strings(settings.allow_internal_targets)
    runtime = FastCrawlerRuntime(settings, guard)
    try:
        with server() as port:
            first = await runtime.fetch(f"http://127.0.0.1:{port}/first")
            crawler = runtime._crawler
            second = await runtime.fetch(f"http://127.0.0.1:{port}/second")
            assert first.status_code == 200
            assert first.title == "Smoke"
            assert second.status_code == 200
            assert runtime._crawler is crawler
    finally:
        await runtime.shutdown()
        assert runtime._crawler is None


@pytest.mark.asyncio
async def test_browser_runtime_executes_javascript_and_reuses_runtime():
    settings = Settings(allow_internal_targets=["127.0.0.1"])
    guard = UrlGuard.from_strings(settings.allow_internal_targets)
    runtime = BrowserCrawlerRuntime(settings, guard)
    try:
        with server() as port:
            first = await runtime.fetch(f"http://127.0.0.1:{port}/first")
            crawler = runtime._crawler
            second = await runtime.fetch(f"http://127.0.0.1:{port}/second")
            assert "Rendered" in first.text
            assert "Rendered" in second.text
            assert runtime._crawler is crawler
    finally:
        await runtime.shutdown()
        assert runtime._crawler is None


@pytest.mark.asyncio
async def test_fast_runtime_blocks_private_redirect_before_second_request():
    settings = Settings(
        allow_internal_targets=["localhost"],
        fetch_wait_timeout_seconds=10,
    )
    guard = UrlGuard.from_strings(settings.allow_internal_targets)
    runtime = FastCrawlerRuntime(settings, guard)
    try:
        with server() as port:
            with pytest.raises(UnsafeUrlError):
                await runtime.fetch(f"http://localhost:{port}/redirect")
            assert Handler.secret_hits == 0
    finally:
        await runtime.shutdown()


@pytest.mark.asyncio
async def test_browser_runtime_blocks_private_redirect_before_second_request():
    settings = Settings(
        allow_internal_targets=["localhost"],
        fetch_wait_timeout_seconds=10,
    )
    guard = UrlGuard.from_strings(settings.allow_internal_targets)
    runtime = BrowserCrawlerRuntime(settings, guard)
    try:
        with server() as port:
            caught: Exception | None = None
            try:
                await runtime.fetch(f"http://localhost:{port}/redirect")
            except Exception as exc:
                caught = exc
            assert caught is not None
            assert Handler.secret_hits == 0
    finally:
        await runtime.shutdown()
