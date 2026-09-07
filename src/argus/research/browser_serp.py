from __future__ import annotations

from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

from bs4 import BeautifulSoup

from argus.config import Settings
from argus.contracts.models import CollectionRequest
from argus.crawler.fast.runtime import FastCrawlerRuntime
from argus.research.discovery import DiscoveryBlockedError, DiscoveryHit


class DuckDuckGoFastDiscoveryProvider:
    """Low-volume HTTP discovery through DuckDuckGo's no-JS HTML endpoint.

    Search navigation stays in the FAST runtime because DuckDuckGo returns an
    anti-bot challenge to headless Chromium for the same query. The provider never
    treats snippets as Evidence and never attempts to bypass access challenges.
    Destination pages are fetched later by GenericWebAdapter, which retains the
    normal FAST -> BROWSER escalation policy.
    """

    name = "duckduckgo_fast"
    base_url = "https://html.duckduckgo.com/html/"

    def __init__(self, settings: Settings, fast: FastCrawlerRuntime) -> None:
        self.settings = settings
        self.fast = fast

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
            url = f"{self.base_url}?{urlencode({'q': value})}"
            fetched = await self.fast.fetch(url)
            if fetched.blocked or self._looks_blocked(fetched.text):
                raise DiscoveryBlockedError(
                    "DuckDuckGo FAST discovery returned an anti-bot/access challenge"
                )
            for hit in self._extract_hits(
                fetched.text,
                self.settings.browser_serp_max_results_per_query,
                query=value,
            ):
                if hit.url in seen:
                    continue
                seen.add(hit.url)
                hits.append(hit)
        return hits

    async def health(self) -> dict[str, object]:
        return {"provider": self.name, "status": "configured"}

    @classmethod
    def _extract_hits(
        cls,
        html: str,
        limit: int,
        *,
        query: str | None = None,
    ) -> list[DiscoveryHit]:
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
                    query=query,
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


class MojeekFastDiscoveryProvider:
    """No-key HTML discovery fallback through Mojeek's public search interface.

    Mojeek is intentionally a secondary provider. It is queried only after an earlier
    discovery provider failed to produce usable destinations. Search-result text remains
    navigation metadata, never Evidence. Access challenges are reported as blocked and are
    never solved or bypassed.
    """

    name = "mojeek_fast"
    base_url = "https://www.mojeek.com/search"

    def __init__(self, settings: Settings, fast: FastCrawlerRuntime) -> None:
        self.settings = settings
        self.fast = fast

    async def discover(
        self,
        queries: list[str],
        request: CollectionRequest,
    ) -> list[DiscoveryHit]:
        del request
        hits: list[DiscoveryHit] = []
        seen: set[str] = set()
        for query in queries:
            value = self._normalize_query(query)[:499]
            if not value:
                continue
            url = f"{self.base_url}?{urlencode({'q': value})}"
            fetched = await self.fast.fetch(url)
            if fetched.blocked or self._looks_blocked(fetched.text):
                raise DiscoveryBlockedError(
                    "Mojeek FAST discovery returned an anti-bot/access challenge"
                )
            for hit in self._extract_hits(
                fetched.text,
                self.settings.browser_serp_max_results_per_query,
                query=value,
            ):
                if hit.url in seen:
                    continue
                seen.add(hit.url)
                hits.append(hit)
        return hits

    async def health(self) -> dict[str, object]:
        return {"provider": self.name, "status": "configured"}

    @staticmethod
    def _normalize_query(query: str) -> str:
        # Mojeek has historically treated some quoted queries as bot-like/invalid. Exact
        # quoting is not required for ARGUS source discovery; keep lexical terms and
        # operators such as site: while avoiding that fragile syntax.
        return " ".join(query.replace('"', " ").split()).strip()

    @classmethod
    def _extract_hits(
        cls,
        html: str,
        limit: int,
        *,
        query: str | None = None,
    ) -> list[DiscoveryHit]:
        soup = BeautifulSoup(html, "html.parser")
        hits: list[DiscoveryHit] = []
        seen: set[str] = set()

        # Mojeek's public SERP has used ul.results-standard with either a.ob as the
        # destination anchor or an h2 anchor. Supporting both shapes makes the parser
        # tolerant of the documented layout variants without scraping navigation links.
        items = list(soup.select("ul.results-standard > li"))
        for item in items:
            destination = item.select_one("a.ob[href]")
            title_anchor = item.select_one("h2 a[href]")
            anchor = destination or title_anchor
            if anchor is None:
                continue
            target = cls._target_url(str(anchor.get("href") or ""))
            if not target or target in seen:
                continue
            seen.add(target)
            title = (
                title_anchor.get_text(" ", strip=True)
                if title_anchor is not None
                else anchor.get_text(" ", strip=True)
            )
            hits.append(
                DiscoveryHit(
                    url=target,
                    provider=cls.name,
                    title=title or None,
                    engines=["mojeek"],
                    rank=len(hits) + 1,
                    query=query,
                )
            )
            if len(hits) >= limit:
                return hits

        if hits:
            return hits

        # Conservative fallback for a minor markup change: only h2 result links are
        # considered and Mojeek-owned navigation URLs are rejected below.
        for anchor in soup.select("h2 a[href]"):
            target = cls._target_url(str(anchor.get("href") or ""))
            if not target or target in seen:
                continue
            seen.add(target)
            hits.append(
                DiscoveryHit(
                    url=target,
                    provider=cls.name,
                    title=anchor.get_text(" ", strip=True) or None,
                    engines=["mojeek"],
                    rank=len(hits) + 1,
                    query=query,
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
        if host == "mojeek.com" or host.endswith(".mojeek.com"):
            return None
        return absolute

    @staticmethod
    def _looks_blocked(html: str) -> bool:
        sample = BeautifulSoup(html[:200_000], "html.parser").get_text(" ", strip=True).lower()
        markers = (
            "your network appears to be sending automated queries",
            "search-wall",
            "search wall",
            "altcha",
            "confirm you are human",
            "verify you are human",
            "403 - forbidden",
        )
        return any(marker in sample for marker in markers)
