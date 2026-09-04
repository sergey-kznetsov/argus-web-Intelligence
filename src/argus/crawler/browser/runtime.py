from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urljoin, urlsplit

from argus.config import Settings
from argus.crawler.block_detection import looks_like_blocked_page
from argus.crawler.lifecycle import FetchBroker
from argus.crawler.models import FetchResult
from argus.crawler.request_manager import build_request_manager
from argus.recipes.executor import PlaywrightRecipeExecutor
from argus.recipes.models import SiteRecipe
from argus.security.urls import UnsafeUrlError, UrlGuard


class BrowserCrawlerRuntime:
    def __init__(self, settings: Settings, url_guard: UrlGuard) -> None:
        self.settings = settings
        self.url_guard = url_guard
        self.recipe_executor = PlaywrightRecipeExecutor(url_guard)
        self._broker = FetchBroker()
        self._crawler: Any | None = None
        self._run_task: asyncio.Task[Any] | None = None
        self._start_lock = asyncio.Lock()
        self._recipes: dict[str, SiteRecipe] = {}

    async def fetch(self, url: str, recipe: SiteRecipe | None = None) -> FetchResult:
        await self.url_guard.validate(url)
        await self._ensure_started()
        from crawlee import Request

        key, future = self._broker.create(url)
        if recipe is not None:
            self._recipes[key] = recipe
        try:
            request = Request.from_url(url, unique_key=key)
            await self._crawler.add_requests([request])
            return await asyncio.wait_for(future, timeout=self.settings.fetch_wait_timeout_seconds)
        except TimeoutError as exc:
            raise TimeoutError("BROWSER runtime result timeout") from exc
        finally:
            self._recipes.pop(key, None)
            self._broker.discard(key)

    async def shutdown(self) -> None:
        crawler, run_task = self._crawler, self._run_task
        self._crawler = None
        self._run_task = None
        self._recipes.clear()
        self._broker.reject_all(RuntimeError("BROWSER runtime is shutting down"))
        if crawler is not None:
            crawler.stop("ARGUS BROWSER shutdown")
        if run_task is not None:
            await asyncio.gather(run_task, return_exceptions=True)

    @staticmethod
    def security_options() -> dict[str, object]:
        """Return the browser isolation contract passed to Crawlee/Playwright."""
        return {
            "browser_type": "chromium",
            "use_incognito_pages": True,
            "browser_launch_options": {
                # Keep Chromium's process sandbox enabled. ARGUS never opts into
                # --no-sandbox; host-level browser isolation is still a deployment duty.
                "chromium_sandbox": True,
            },
            "browser_new_context_options": {
                "accept_downloads": False,
                "service_workers": "block",
                "ignore_https_errors": False,
            },
        }

    async def _ensure_started(self) -> None:
        if self._crawler is not None and self._run_task is not None and not self._run_task.done():
            return
        async with self._start_lock:
            if self._crawler is not None and self._run_task is not None and not self._run_task.done():
                return
            try:
                from crawlee import ConcurrencySettings, SkippedReason
                from crawlee.crawlers import (
                    BasicCrawlingContext,
                    PlaywrightCrawler,
                    PlaywrightCrawlingContext,
                    PlaywrightPreNavCrawlingContext,
                )
                from crawlee.errors import HttpStatusCodeError, SessionError
            except ImportError as exc:
                raise RuntimeError("Crawlee Playwright extra is required for BROWSER runtime") from exc

            storage_client, request_manager = await build_request_manager(
                self.settings, "argus-browser-runtime"
            )
            crawler = PlaywrightCrawler(
                request_manager=request_manager,
                storage_client=storage_client,
                headless=True,
                keep_alive=True,
                max_request_retries=2,
                use_session_pool=True,
                retry_on_blocked=False,
                ignore_http_error_status_codes=[401, 403, 429],
                concurrency_settings=ConcurrencySettings(
                    max_concurrency=self.settings.browser_max_concurrency,
                    desired_concurrency=self.settings.browser_max_concurrency,
                    max_tasks_per_minute=self.settings.browser_max_requests_per_minute,
                ),
                request_handler_timeout=self._duration(self.settings.browser_timeout_seconds),
                navigation_timeout=self._duration(self.settings.browser_timeout_seconds),
                respect_robots_txt_file=True,
                configure_logging=False,
                **self.security_options(),
            )

            @crawler.on_skipped_request
            async def skipped_request_handler(url: str, reason: SkippedReason) -> None:
                self._broker.reject_skipped(url, reason)

            @crawler.pre_navigation_hook
            async def secure_subrequests(context: PlaywrightPreNavCrawlingContext) -> None:
                async def route_handler(route) -> None:
                    request_url = route.request.url
                    if urlsplit(request_url).scheme in {"data", "blob", "about"}:
                        await route.continue_()
                        return
                    try:
                        await self.url_guard.validate(request_url)
                    except UnsafeUrlError:
                        await route.abort("blockedbyclient")
                        return

                    # Playwright may follow a navigation redirect before a normal route
                    # handler gets a chance to reject the next hop. For document requests,
                    # fetch exactly one hop ourselves, validate Location while the browser
                    # has not seen the redirect yet, then fulfill the intercepted request.
                    # This preserves the network boundary: an unsafe redirect target is
                    # never contacted by Chromium.
                    if route.request.resource_type == "document":
                        response = await route.fetch(max_redirects=0)
                        if 300 <= response.status < 400:
                            location = response.headers.get("location")
                            if location:
                                redirect_url = urljoin(request_url, location)
                                try:
                                    await self.url_guard.validate_redirect(
                                        request_url,
                                        redirect_url,
                                    )
                                except UnsafeUrlError:
                                    await response.dispose()
                                    await route.abort("blockedbyclient")
                                    return
                        await route.fulfill(response=response)
                        return

                    await route.continue_()

                await context.page.route("**/*", route_handler)

            @crawler.router.default_handler
            async def handler(context: PlaywrightCrawlingContext) -> None:
                key = context.request.unique_key
                recipe = self._recipes.get(key)
                recipe_extracted: list[dict[str, Any]] = []
                document_response = context.response

                def track_document_response(response: Any) -> None:
                    nonlocal document_response
                    try:
                        request = response.request
                        if (
                            request.resource_type == "document"
                            and response.frame == context.page.main_frame
                        ):
                            document_response = response
                    except Exception:
                        return

                context.page.on("response", track_document_response)
                if recipe is not None:
                    recipe_extracted = await self.recipe_executor.execute(context.page, recipe)
                requested_url = context.request.url
                final_url = context.page.url
                await self.url_guard.validate_redirect(requested_url, final_url)
                html = await context.page.content()
                if len(html.encode("utf-8", errors="replace")) > self.settings.max_response_bytes:
                    raise ValueError("browser content exceeds configured limit")
                title = await context.page.title()
                links = await context.page.locator("a[href]").evaluate_all(
                    "els => els.map(a => a.href).filter(Boolean).slice(0, 1000)"
                )
                text_sample = (await context.page.locator("body").inner_text())[:50_000]
                status_code = document_response.status
                content_type = await document_response.header_value("content-type") or "text/html"
                blocked = status_code in {401, 403, 429} or looks_like_blocked_page(
                    text_sample,
                    content_type,
                )
                metadata: dict[str, object] = {}
                if recipe is not None:
                    metadata = {
                        "recipe_id": recipe.recipe_id,
                        "recipe_version": recipe.version,
                        "recipe_extracted": recipe_extracted,
                    }
                self._broker.resolve(
                    key,
                    FetchResult(
                        url=requested_url,
                        final_url=final_url,
                        status_code=status_code,
                        content_type=content_type,
                        text=html,
                        title=title,
                        links=[urljoin(final_url, item) for item in links],
                        blocked=blocked,
                        runtime="browser_recipe" if recipe is not None else "browser",
                        metadata=metadata,
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
            self._run_task = asyncio.create_task(crawler.run([]), name="argus:crawlee-browser")
            self._run_task.add_done_callback(self._on_run_done)
            await asyncio.sleep(0)

    def _on_run_done(self, task: asyncio.Task[Any]) -> None:
        if task.cancelled():
            self._broker.reject_all(RuntimeError("BROWSER crawler stopped unexpectedly"))
            return
        error = task.exception()
        if error is not None:
            self._broker.reject_all(error)

    @staticmethod
    def _duration(seconds: float):
        from datetime import timedelta

        return timedelta(seconds=seconds)
