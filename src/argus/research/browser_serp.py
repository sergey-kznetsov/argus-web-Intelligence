from __future__ import annotations

from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup

from argus.config import Settings
from argus.contracts.models import CollectionRequest
from argus.crawler.browser.runtime import BrowserCrawlerRuntime
from argus.recipes.models import RecipeStep, SiteRecipe
from argus.research.discovery import DiscoveryBlockedError, DiscoveryHit


class DuckDuckGoBrowserDiscoveryProvider:
    """Low-volume browser fallback for public web discovery.

    The provider submits DuckDuckGo's no-JS HTML form through the existing
    Playwright runtime. It never treats snippets as evidence and never attempts to
    bypass an anti-bot challenge. Destination pages are fetched later by
    GenericWebAdapter before any Observation/Evidence is created.
    """

    name = "duckduckgo_browser"
    base_url = "https://html.duckduckgo.com/html/"

    def __init__(self, settings: Settings, browser: BrowserCrawlerRuntime) -> None:
        self.settings = settings
        self.browser = browser

    async def discover(
        self,
        queries: list[str],
        request: CollectionRequest,
    ) -> list[DiscoveryHit]:
        del request
        hits: list[DiscoveryHit] = []
        seen: set[str] = set()
        for query in queries:
            value = query.strip()[:499]
            if not value:
                continue
            recipe = SiteRecipe(
                domain="html.duckduckgo.com",
                goal="serp_discovery",
                steps=[
                    RecipeStep(action="fill", selector='input[name="q"]', value=value),
                    RecipeStep(action="press", selector='input[name="q"]', value="Enter"),
                    RecipeStep(
                        action="wait",
                        data={
                            "state": "domcontentloaded",
                            "timeout_ms": int(self.settings.browser_timeout_seconds * 1_000),
                        },
                    ),
                    RecipeStep(
                        action="wait",
                        data={"milliseconds": self.settings.browser_serp_wait_ms},
                    ),
                ],
            )
            fetched = await self.browser.fetch(self.base_url, recipe=recipe)
            if fetched.blocked or self._looks_blocked(fetched.text):
                raise DiscoveryBlockedError(
                    "DuckDuckGo browser discovery returned an anti-bot/access challenge"
                )
            for hit in self._extract_hits(
                fetched.text,
                self.settings.browser_serp_max_results_per_query,
            ):
                if hit.url in seen:
                    continue
                seen.add(hit.url)
                hits.append(hit)
        return hits

    async def health(self) -> dict[str, object]:
        return {"provider": self.name, "status": "configured"}

    @classmethod
    def _extract_hits(cls, html: str, limit: int) -> list[DiscoveryHit]:
        soup = BeautifulSoup(html, "html.parser")
        hits: list[DiscoveryHit] = []
        seen: set[str] = set()
        for anchor in soup.find_all("a", href=True):
            target = cls._target_url(str(anchor.get("href") or ""))
            if not target or target in seen:
                continue
            seen.add(target)
            hits.append(
                DiscoveryHit(
                    url=target,
                    provider=cls.name,
                    title=anchor.get_text(" ", strip=True) or None,
                    engines=["duckduckgo"],
                    rank=len(hits) + 1,
                )
            )
            if len(hits) >= limit:
                break
        return hits

    @classmethod
    def _target_url(cls, href: str) -> str | None:
        absolute = urljoin(cls.base_url, href)
        parsed = urlsplit(absolute)
        host = (parsed.hostname or "").lower().strip(".")
        if parsed.scheme not in {"http", "https"} or not host:
            return None
        if host == "duckduckgo.com" or host.endswith(".duckduckgo.com"):
            target = parse_qs(parsed.query).get("uddg", [None])[0]
            if not target:
                return None
            target_parsed = urlsplit(target)
            if target_parsed.scheme not in {"http", "https"} or not target_parsed.hostname:
                return None
            return target
        return absolute

    @staticmethod
    def _looks_blocked(html: str) -> bool:
        sample = BeautifulSoup(html[:200_000], "html.parser").get_text(" ", strip=True).lower()
        markers = (
            "unfortunately, bots use duckduckgo too",
            "please complete the following challenge",
            "verify you are a human",
            "captcha",
        )
        return any(marker in sample for marker in markers)
