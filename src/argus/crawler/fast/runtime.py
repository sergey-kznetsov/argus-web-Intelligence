from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from argus.config import Settings
from argus.crawler.lifecycle import FetchBroker
from argus.crawler.models import FetchResult
from argus.crawler.request_manager import build_request_manager
from argus.security.urls import UrlGuard


class FastCrawlerRuntime:
    def __init__(self, settings: Settings, url_guard: UrlGuard) -> None:
        self.settings = settings
        self.url_guard = url_guard
        self._broker = FetchBroker()
        self._crawler: Any | None = None
        self._run_task: asyncio.Task[Any] | None = None
        self._start_lock = asyncio.Lock()

    async def fetch(self, url: str) -> FetchResult:
        await self.url_guard.validate(url)
        await self._ensure_started()
        from crawlee import Request

        key, future = self._broker.create()
        try:
            request = Request.from_url(url, unique_key=key)
            await self._crawler.add_requests([request])
            return await asyncio.wait_for(
                future,
                timeout=self.settings.fetch_wait_timeout_seconds,
            )
        except TimeoutError as exc:
            raise TimeoutError("FAST runtime result timeout") from exc
        finally:
            self._broker.discard(key)

    async def shutdown(self) -> None:
        crawler, run_task = self._crawler, self._run_task
        self._crawler = None
        self._run_task = None
        self._broker.reject_all(RuntimeError("FAST runtime is shutting down"))
        if crawler is not None:
            crawler.stop("ARGUS FAST shutdown")
        if run_task is not None:
            await asyncio.gather(run_task, return_exceptions=True)

    async def _ensure_started(self) -> None:
        if self._crawler is not None and self._run_task is not None and not self._run_task.done():
            return
        async with self._start_lock:
            if self._crawler is not None and self._run_task is not None and not self._run_task.done():
                return
            try:
                from crawlee import ConcurrencySettings
                from crawlee.crawlers import BasicCrawlingContext, HttpCrawler, HttpCrawlingContext
                from crawlee.errors import HttpStatusCodeError, SessionError
                from crawlee.http_clients import HttpxHttpClient
            except ImportError as exc:
                raise RuntimeError("Crawlee with the httpx extra is required for FAST runtime") from exc

            storage_client, request_manager = await build_request_manager(
                self.settings,
                "argus-fast-runtime",
            )

            async def validate_outbound_request(request) -> None:
                # HTTPX executes request hooks for the initial request and every redirect hop.
                # Validate before the transport sees the request, not after a redirect is fetched.
                await self.url_guard.validate(str(request.url))

            http_client = HttpxHttpClient(
                event_hooks={"request": [validate_outbound_request]},
                follow_redirects=True,
                max_redirects=self.settings.http_max_redirects,
                trust_env=False,
            )
            crawler = HttpCrawler(
                request_manager=request_manager,
                storage_client=storage_client,
                http_client=http_client,
                keep_alive=True,
                max_request_retries=3,
                use_session_pool=True,
                retry_on_blocked=False,
                ignore_http_error_status_codes=[401, 403, 429],
                concurrency_settings=ConcurrencySettings(
                    max_concurrency=self.settings.max_concurrency,
                    desired_concurrency=self.settings.max_concurrency,
                    max_tasks_per_minute=self.settings.fast_max_requests_per_minute,
                ),
                request_handler_timeout=self._duration(self.settings.http_timeout_seconds),
                respect_robots_txt_file=True,
                configure_logging=False,
            )

            @crawler.router.default_handler
            async def handler(context: HttpCrawlingContext) -> None:
                response = context.http_response
                requested_url = context.request.url
                final_url = context.request.loaded_url or requested_url
                # Defense in depth. Each hop was already checked by the HTTPX request hook.
                await self.url_guard.validate_redirect(requested_url, final_url)
                content_length = self._content_length(response.headers.get("content-length"))
                if content_length is not None and content_length > self.settings.max_response_bytes:
                    raise ValueError("response Content-Length exceeds configured limit")
                body = await response.read()
                if len(body) > self.settings.max_response_bytes:
                    raise ValueError("response body exceeds configured limit")
                content_type = response.headers.get("content-type")
                text = body.decode(self._charset(content_type), errors="replace")
                blocked = response.status_code in {401, 403, 429} or self._looks_blocked(text)
                title, links = self._html_metadata(text, final_url, content_type)
                self._broker.resolve(
                    context.request.unique_key,
                    FetchResult(
                        url=requested_url,
                        final_url=final_url,
                        status_code=response.status_code,
                        content_type=content_type,
                        text=text,
                        title=title,
                        links=links,
                        blocked=blocked,
                        runtime="fast",
                    ),
                )

            @crawler.error_handler
            async def error_handler(context: BasicCrawlingContext, error: Exception) -> None:
                if not isinstance(error, (SessionError, HttpStatusCodeError)):
                    context.request.no_retry = True

            @crawler.failed_request_handler
            async def failed_handler(context: BasicCrawlingContext, error: Exception) -> None:
                self._broker.reject(context.request.unique_key, error)

            self._crawler = crawler
            self._run_task = asyncio.create_task(crawler.run([]), name="argus:crawlee-fast")
            self._run_task.add_done_callback(self._on_run_done)
            await asyncio.sleep(0)

    def _on_run_done(self, task: asyncio.Task[Any]) -> None:
        if task.cancelled():
            self._broker.reject_all(RuntimeError("FAST crawler stopped unexpectedly"))
            return
        error = task.exception()
        if error is not None:
            self._broker.reject_all(error)

    @staticmethod
    def _duration(seconds: float):
        from datetime import timedelta

        return timedelta(seconds=seconds)

    @staticmethod
    def _content_length(value: str | None) -> int | None:
        if not value:
            return None
        try:
            return max(0, int(value))
        except ValueError:
            return None

    @staticmethod
    def _charset(content_type: str | None) -> str:
        if content_type:
            for part in content_type.split(";")[1:]:
                key, separator, value = part.strip().partition("=")
                if separator and key.lower() == "charset" and value.strip():
                    return value.strip().strip('"\'')
        return "utf-8"

    @staticmethod
    def _html_metadata(
        text: str,
        base_url: str,
        content_type: str | None,
    ) -> tuple[str | None, list[str]]:
        if content_type and "html" not in content_type.lower():
            return None, []
        soup = BeautifulSoup(text, "html.parser")
        title = soup.title.get_text(" ", strip=True) if soup.title else None
        links: list[str] = []
        seen: set[str] = set()
        for tag in soup.find_all("a", href=True):
            link = urljoin(base_url, tag["href"])
            if link.startswith(("http://", "https://")) and link not in seen:
                seen.add(link)
                links.append(link)
        return title, links

    @staticmethod
    def _looks_blocked(text: str) -> bool:
        sample = text[:50_000].lower()
        markers = ("captcha", "verify you are human", "access denied", "robot check")
        return any(marker in sample for marker in markers)
