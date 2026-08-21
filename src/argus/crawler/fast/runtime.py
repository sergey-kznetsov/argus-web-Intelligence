from __future__ import annotations

from urllib.parse import urljoin

from bs4 import BeautifulSoup

from argus.config import Settings
from argus.crawler.models import FetchResult
from argus.security.urls import UrlGuard


class FastCrawlerRuntime:
    def __init__(self, settings: Settings, url_guard: UrlGuard) -> None:
        self.settings = settings
        self.url_guard = url_guard

    async def fetch(self, url: str) -> FetchResult:
        await self.url_guard.validate(url)
        try:
            from crawlee.crawlers import HttpCrawler, HttpCrawlingContext
        except ImportError as exc:
            raise RuntimeError("Crawlee is required for FAST runtime") from exc

        result: FetchResult | None = None
        crawler = HttpCrawler(
            max_requests_per_crawl=1,
            max_request_retries=3,
            use_session_pool=True,
            request_handler_timeout=self._duration(self.settings.http_timeout_seconds),
            respect_robots_txt_file=True,
        )

        @crawler.router.default_handler
        async def handler(context: HttpCrawlingContext) -> None:
            nonlocal result
            response = context.http_response
            final_url = context.request.loaded_url or context.request.url
            await self.url_guard.validate_redirect(url, final_url)
            body = await response.read()
            if len(body) > self.settings.max_response_bytes:
                raise ValueError("response body exceeds configured limit")
            content_type = response.headers.get("content-type")
            text = body.decode("utf-8", errors="replace")
            blocked = response.status_code in {401, 403, 429} or self._looks_blocked(text)
            title, links = self._html_metadata(text, final_url, content_type)
            result = FetchResult(
                url=url,
                final_url=final_url,
                status_code=response.status_code,
                content_type=content_type,
                text=text,
                title=title,
                links=links,
                blocked=blocked,
                runtime="fast",
            )

        await crawler.run([url])
        if result is None:
            raise RuntimeError("FAST runtime returned no result")
        return result

    @staticmethod
    def _duration(seconds: float):
        from datetime import timedelta
        return timedelta(seconds=seconds)

    @staticmethod
    def _html_metadata(text: str, base_url: str, content_type: str | None) -> tuple[str | None, list[str]]:
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
