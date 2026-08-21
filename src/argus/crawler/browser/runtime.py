from __future__ import annotations

from urllib.parse import urljoin

from argus.config import Settings
from argus.crawler.models import FetchResult
from argus.security.urls import UrlGuard


class BrowserCrawlerRuntime:
    def __init__(self, settings: Settings, url_guard: UrlGuard) -> None:
        self.settings = settings
        self.url_guard = url_guard

    async def fetch(self, url: str) -> FetchResult:
        await self.url_guard.validate(url)
        try:
            from crawlee.crawlers import PlaywrightCrawler, PlaywrightCrawlingContext
        except ImportError as exc:
            raise RuntimeError("Crawlee Playwright extra is required for BROWSER runtime") from exc

        result: FetchResult | None = None
        crawler = PlaywrightCrawler(
            headless=True,
            max_requests_per_crawl=1,
            max_request_retries=2,
            use_session_pool=True,
            request_handler_timeout=self._duration(self.settings.browser_timeout_seconds),
            retry_on_blocked=False,
            respect_robots_txt_file=True,
        )

        @crawler.router.default_handler
        async def handler(context: PlaywrightCrawlingContext) -> None:
            nonlocal result
            final_url = context.page.url
            await self.url_guard.validate_redirect(url, final_url)
            html = await context.page.content()
            if len(html.encode("utf-8", errors="replace")) > self.settings.max_response_bytes:
                raise ValueError("browser content exceeds configured limit")
            title = await context.page.title()
            links = await context.page.locator("a[href]").evaluate_all(
                "els => els.map(a => a.href).filter(Boolean).slice(0, 1000)"
            )
            text_sample = (await context.page.locator("body").inner_text())[:50_000].lower()
            blocked = any(x in text_sample for x in ("captcha", "verify you are human", "access denied"))
            result = FetchResult(
                url=url,
                final_url=final_url,
                status_code=200,
                content_type="text/html",
                text=html,
                title=title,
                links=[urljoin(final_url, item) for item in links],
                blocked=blocked,
                runtime="browser",
            )

        await crawler.run([url])
        if result is None:
            raise RuntimeError("BROWSER runtime returned no result")
        return result

    @staticmethod
    def _duration(seconds: float):
        from datetime import timedelta
        return timedelta(seconds=seconds)
