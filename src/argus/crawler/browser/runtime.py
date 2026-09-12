from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urljoin, urlsplit

from argus.config import Settings
from argus.crawler.block_detection import (
    looks_like_blocked_page,
    looks_like_captcha_page,
    looks_like_transient_challenge_page,
)
from argus.crawler.lifecycle import FetchBroker
from argus.crawler.models import FetchResult
from argus.crawler.request_manager import build_request_manager
from argus.human_interaction import FileCaptchaBroker
from argus.recipes.executor import PlaywrightRecipeExecutor
from argus.recipes.models import SiteRecipe
from argus.security.urls import UnsafeUrlError, UrlGuard


class BrowserCrawlerRuntime:
    captcha_manual_timeout_seconds = 900.0
    captcha_max_manual_attempts = 3
    challenge_passive_wait_seconds = 8.0
    challenge_reload_wait_seconds = 5.0
    _captcha_input_selectors = (
        "input[name*='captcha' i]:visible",
        "input[id*='captcha' i]:visible",
        "input[placeholder*='captcha' i]:visible",
        "input[aria-label*='captcha' i]:visible",
        "input[name*='verification' i]:visible",
        "input[id*='verification' i]:visible",
        "input[placeholder*='код' i]:visible",
        "input[aria-label*='код' i]:visible",
    )

    def __init__(self, settings: Settings, url_guard: UrlGuard) -> None:
        self.settings = settings
        self.url_guard = url_guard
        self.recipe_executor = PlaywrightRecipeExecutor(url_guard)
        self._broker = FetchBroker()
        self._captcha = FileCaptchaBroker(settings.db_path.parent / "human_interaction")
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
            timeout = self.settings.fetch_wait_timeout_seconds + self.captcha_manual_timeout_seconds
            return await asyncio.wait_for(future, timeout=timeout)
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
                "chromium_sandbox": True,
            },
            "browser_new_context_options": {
                "accept_downloads": False,
                "service_workers": "block",
                "ignore_https_errors": False,
            },
        }

    @staticmethod
    def _is_navigation_context_error(error: Exception) -> bool:
        message = str(error).casefold()
        return any(
            marker in message
            for marker in (
                "execution context was destroyed",
                "cannot find context with specified id",
                "most likely because of a navigation",
            )
        )

    @staticmethod
    async def _wait_for_dom_settle(page: Any) -> bool:
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=1500)
        except Exception:
            return False
        return True

    async def _page_links(self, page: Any) -> list[str]:
        for attempt in range(3):
            try:
                values = await page.locator("a[href]").evaluate_all(
                    "els => els.map(a => a.href).filter(Boolean).slice(0, 1000)"
                )
                return [str(item) for item in values if item]
            except Exception as exc:
                if not self._is_navigation_context_error(exc):
                    raise
                if attempt >= 2:
                    return []
                await self._wait_for_dom_settle(page)
                await asyncio.sleep(0.1)
        return []

    async def _page_body_text(self, page: Any, html: str) -> str:
        for attempt in range(3):
            try:
                return (await page.locator("body").inner_text())[:50_000]
            except Exception as exc:
                if not self._is_navigation_context_error(exc):
                    raise
                if attempt >= 2:
                    return html[:50_000]
                await self._wait_for_dom_settle(page)
                await asyncio.sleep(0.1)
        return html[:50_000]

    async def _captcha_input_selector(self, page: Any) -> str | None:
        for selector in self._captcha_input_selectors:
            try:
                if await page.locator(selector).count() > 0:
                    return selector
            except Exception:
                continue
        return None

    async def _challenge_state(self, page: Any) -> tuple[bool, bool]:
        try:
            html = await page.content()
            body = await self._page_body_text(page, html)
        except Exception:
            return True, False
        return (
            looks_like_captcha_page(body, "text/html"),
            looks_like_transient_challenge_page(body, "text/html"),
        )

    async def _wait_for_normal_browser_clearance(
        self,
        page: Any,
        *,
        timeout_seconds: float,
    ) -> bool:
        """Allow a site challenge to clear through ordinary browser execution only."""

        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            captcha, transient = await self._challenge_state(page)
            if not captcha and not transient:
                return True
            await asyncio.sleep(0.5)
        return False

    async def _attempt_automatic_challenge_recovery(
        self,
        page: Any,
        *,
        explicit_captcha: bool,
        transient_challenge: bool,
    ) -> tuple[bool, str]:
        """Use bounded normal browser behaviour before asking the user.

        This is deliberately not a CAPTCHA solver. ARGUS only keeps the same real browser
        session alive so JavaScript/cookies can finish a managed challenge. For transient
        interstitials it may perform one ordinary reload. It never derives CAPTCHA answers,
        clicks verification widgets, invokes solver services, or alters browser fingerprints.
        """

        if not explicit_captcha and not transient_challenge:
            return False, "not_applicable"

        if await self._wait_for_normal_browser_clearance(
            page,
            timeout_seconds=self.challenge_passive_wait_seconds,
        ):
            return True, "passive_browser_wait"

        if explicit_captcha:
            return False, "explicit_captcha_requires_user"

        try:
            await page.reload(wait_until="domcontentloaded", timeout=5000)
        except Exception:
            return False, "transient_reload_failed"

        if await self._wait_for_normal_browser_clearance(
            page,
            timeout_seconds=self.challenge_reload_wait_seconds,
        ):
            return True, "bounded_browser_reload"
        return False, "challenge_persisted"

    async def _submit_captcha_answer(self, page: Any, field: Any, answer: str) -> None:
        await field.fill(answer)
        for selector in (
            "button[type='submit']:visible",
            "input[type='submit']:visible",
            "button:has-text('Проверить'):visible",
            "button:has-text('Продолжить'):visible",
            "button:has-text('Отправить'):visible",
            "button:has-text('Submit'):visible",
            "button:has-text('Continue'):visible",
        ):
            try:
                candidate = page.locator(selector).first
                if await candidate.count() > 0:
                    await candidate.click()
                    return
            except Exception:
                continue
        await field.press("Enter")

    async def _captcha_cleared(self, page: Any) -> bool:
        deadline = asyncio.get_running_loop().time() + 10.0
        while asyncio.get_running_loop().time() < deadline:
            try:
                html = await page.content()
                body = await self._page_body_text(page, html)
            except Exception:
                await asyncio.sleep(0.25)
                continue
            if not looks_like_captcha_page(body, "text/html"):
                return True
            await asyncio.sleep(0.25)
        return False

    async def _handle_manual_captcha(self, page: Any, url: str) -> bool:
        """Pause the live browser session and hand the remaining challenge to the user."""

        for _attempt in range(self.captcha_max_manual_attempts):
            selector = await self._captcha_input_selector(page)
            screenshot = await page.screenshot(type="png", full_page=False)
            kind = "text" if selector is not None else "interactive"
            challenge = self._captcha.create(
                url=url,
                screenshot=screenshot,
                kind=kind,
                prompt=(
                    "Введите символы CAPTCHA, показанные на скриншоте."
                    if kind == "text"
                    else "Требуется ручное прохождение интерактивной проверки в браузере."
                ),
                input_selector=selector,
            )
            challenge_id = str(challenge["challenge_id"])
            if selector is None:
                self._captcha.mark_failed(
                    challenge_id,
                    "interactive challenge requires user browser interaction",
                    status="interactive_required",
                )
                return False

            try:
                answer = await self._captcha.wait_for_answer(
                    challenge_id,
                    timeout_seconds=self.captcha_manual_timeout_seconds,
                )
                field = page.locator(selector).first
                await self._submit_captcha_answer(page, field, answer)
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=5000)
                except Exception:
                    pass
                if await self._captcha_cleared(page):
                    self._captcha.mark_completed(challenge_id)
                    return True
                self._captcha.mark_failed(challenge_id, "CAPTCHA answer was rejected")
            except Exception as exc:
                try:
                    self._captcha.mark_failed(challenge_id, str(exc))
                except Exception:
                    pass
                if isinstance(exc, TimeoutError):
                    return False
        return False

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
                request_handler_timeout=self._duration(
                    self.settings.browser_timeout_seconds + self.captcha_manual_timeout_seconds
                ),
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

                    if route.request.resource_type == "document":
                        response = await route.fetch(max_redirects=0)
                        if 300 <= response.status < 400:
                            location = response.headers.get("location")
                            if location:
                                redirect_url = urljoin(request_url, location)
                                try:
                                    await self.url_guard.validate_redirect(request_url, redirect_url)
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
                links = await self._page_links(context.page)
                text_sample = await self._page_body_text(context.page, html)
                status_code = document_response.status
                content_type = await document_response.header_value("content-type") or "text/html"

                captcha_seen = looks_like_captcha_page(text_sample, content_type)
                transient_seen = looks_like_transient_challenge_page(text_sample, content_type)
                challenge_seen = captcha_seen or transient_seen
                auto_cleared = False
                auto_strategy = "not_applicable"
                manual_attempted = False
                manual_solved = False

                if challenge_seen:
                    auto_cleared, auto_strategy = await self._attempt_automatic_challenge_recovery(
                        context.page,
                        explicit_captcha=captcha_seen,
                        transient_challenge=transient_seen,
                    )
                    final_url = context.page.url
                    await self.url_guard.validate_redirect(requested_url, final_url)
                    html = await context.page.content()
                    if len(html.encode("utf-8", errors="replace")) > self.settings.max_response_bytes:
                        raise ValueError("browser content exceeds configured limit")
                    title = await context.page.title()
                    links = await self._page_links(context.page)
                    text_sample = await self._page_body_text(context.page, html)
                    captcha_remaining = looks_like_captcha_page(text_sample, content_type)
                    transient_remaining = looks_like_transient_challenge_page(
                        text_sample,
                        content_type,
                    )
                    if not auto_cleared and (captcha_remaining or transient_remaining):
                        manual_attempted = True
                        manual_solved = await self._handle_manual_captcha(
                            context.page,
                            final_url,
                        )
                        if manual_solved:
                            final_url = context.page.url
                            await self.url_guard.validate_redirect(requested_url, final_url)
                            html = await context.page.content()
                            if len(html.encode("utf-8", errors="replace")) > self.settings.max_response_bytes:
                                raise ValueError("browser content exceeds configured limit")
                            title = await context.page.title()
                            links = await self._page_links(context.page)
                            text_sample = await self._page_body_text(context.page, html)

                blocked = (
                    status_code in {401, 403, 429}
                    or looks_like_blocked_page(text_sample, content_type)
                )
                metadata: dict[str, object] = {
                    "challenge_detected": challenge_seen,
                    "challenge_transient_detected": transient_seen,
                    "challenge_auto_recovery_attempted": challenge_seen,
                    "challenge_auto_recovered": auto_cleared,
                    "challenge_auto_strategy": auto_strategy,
                    "captcha_detected": captcha_seen,
                    "captcha_manual_attempted": manual_attempted,
                    "captcha_manual_solved": manual_solved,
                    "captcha_solver": "human_input" if manual_attempted else None,
                    "captcha_automatic_solving": False,
                }
                if recipe is not None:
                    metadata.update(
                        {
                            "recipe_id": recipe.recipe_id,
                            "recipe_version": recipe.version,
                            "recipe_extracted": recipe_extracted,
                        }
                    )
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
