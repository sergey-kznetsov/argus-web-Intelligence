from __future__ import annotations

from urllib.parse import urldefrag, urljoin, urlparse

from bs4 import BeautifulSoup

from argus.contracts.models import CollectionRequest, Evidence, EvidenceSource, Observation
from argus.crawler.browser.runtime import BrowserCrawlerRuntime
from argus.crawler.fast.runtime import FastCrawlerRuntime
from argus.history.snapshots import SnapshotService, sha256_text
from argus.security.urls import UnsafeUrlError
from argus.sources.base import SourceResult, SourceTask


class GenericWebAdapter:
    source_id = "generic_web"
    intents = {"*"}

    def __init__(
        self,
        fast: FastCrawlerRuntime,
        browser: BrowserCrawlerRuntime,
        snapshots: SnapshotService,
    ) -> None:
        self.fast = fast
        self.browser = browser
        self.snapshots = snapshots

    async def discover(self, request: CollectionRequest) -> list[SourceTask]:
        return [
            SourceTask(
                source_id=self.source_id,
                goal=request.intents[0],
                url=str(url),
                depth=0,
                metadata={"intents": list(request.intents)},
            )
            for url in request.constraints.seed_urls
        ]

    async def fetch(self, task: SourceTask):
        try:
            result = await self.fast.fetch(task.url)
            if result.blocked or self._needs_browser(result.text):
                return await self.browser.fetch(task.url)
            return result
        except UnsafeUrlError:
            raise
        except Exception:
            return await self.browser.fetch(task.url)

    async def extract(self, task: SourceTask, fetched, request: CollectionRequest) -> SourceResult:
        if fetched.blocked:
            return SourceResult(observations=[], blocked=True)
        snapshot = await self.snapshots.capture(
            self.source_id,
            fetched.final_url,
            fetched.text,
            fetched.content_type,
        )
        text = self._main_text(fetched.text, fetched.content_type)
        observation = Observation(
            collection_id=str(task.metadata.get("collection_id", "")),
            analysis_id=request.analysis_id,
            consumer=request.consumer,
            source=self.source_id,
            source_kind="web_page",
            url=fetched.final_url,
            entity_type="document",
            title=fetched.title,
            text=text[:100_000],
            data={"runtime": fetched.runtime, "status_code": fetched.status_code},
            content_hash=sha256_text(text),
            provenance={"snapshot_id": snapshot.snapshot_id},
            quality={"evidence_backed": True},
        )
        evidence = Evidence(
            observation_id=observation.observation_id,
            type="document",
            text=text[:10_000],
            source=EvidenceSource(
                provider=self.source_id,
                url=fetched.final_url,
                collected_at=observation.collected_at,
                source_id=self.source_id,
            ),
        )
        discovered = self._discovered_tasks(task, fetched, request, observation.collection_id)
        return SourceResult(
            observations=[observation],
            evidence=[evidence],
            discovered_tasks=discovered,
        )

    async def normalize(self, result: SourceResult) -> SourceResult:
        return result

    async def health(self) -> dict[str, object]:
        return {"source_id": self.source_id, "status": "ok"}

    def _discovered_tasks(
        self,
        task: SourceTask,
        fetched,
        request: CollectionRequest,
        collection_id: str,
    ) -> list[SourceTask]:
        discovered: list[SourceTask] = []
        seen: set[str] = set()
        max_depth = request.constraints.max_depth
        allowed = {d.lower().strip(".") for d in request.constraints.allowed_domains}
        denied = {d.lower().strip(".") for d in request.constraints.denied_domains}
        seed_host = (urlparse(fetched.final_url).hostname or "").lower().strip(".")

        for feed_url in self._feed_links(fetched.text, fetched.final_url, fetched.content_type):
            if self._domain_allowed(feed_url, seed_host, allowed, denied):
                key = f"rss_atom:{feed_url}"
                if key not in seen:
                    seen.add(key)
                    discovered.append(
                        SourceTask(
                            source_id="rss_atom",
                            goal=task.goal,
                            url=feed_url,
                            depth=task.depth,
                            metadata={
                                "collection_id": collection_id,
                                "discovered_from": fetched.final_url,
                            },
                        )
                    )

        if task.depth >= max_depth:
            return discovered

        for link in fetched.links[: request.constraints.max_pages]:
            link = urldefrag(link)[0]
            if not link or not self._domain_allowed(link, seed_host, allowed, denied):
                continue
            key = f"{self.source_id}:{link}"
            if key in seen:
                continue
            seen.add(key)
            discovered.append(
                SourceTask(
                    source_id=self.source_id,
                    goal=task.goal,
                    url=link,
                    depth=task.depth + 1,
                    metadata={"collection_id": collection_id},
                )
            )
        return discovered

    @staticmethod
    def _domain_allowed(url: str, seed_host: str, allowed: set[str], denied: set[str]) -> bool:
        domain = (urlparse(url).hostname or "").lower().strip(".")
        if not domain:
            return False
        if any(domain == d or domain.endswith("." + d) for d in denied):
            return False
        if allowed:
            return any(domain == d or domain.endswith("." + d) for d in allowed)
        return domain == seed_host

    @staticmethod
    def _feed_links(html: str, base_url: str, content_type: str | None) -> list[str]:
        if content_type and "html" not in content_type.lower():
            return []
        soup = BeautifulSoup(html, "html.parser")
        links: list[str] = []
        seen: set[str] = set()
        for tag in soup.find_all("link", href=True):
            rel = {str(item).lower() for item in tag.get("rel", [])}
            mime = str(tag.get("type", "")).lower()
            if "alternate" not in rel or mime not in {
                "application/rss+xml",
                "application/atom+xml",
                "application/feed+json",
            }:
                continue
            url = urldefrag(urljoin(base_url, tag["href"]))[0]
            if url.startswith(("http://", "https://")) and url not in seen:
                seen.add(url)
                links.append(url)
        return links

    @staticmethod
    def _needs_browser(html: str) -> bool:
        sample = html[:100_000].lower()
        return (
            "__next_data__" in sample
            or 'id="root"></div>' in sample
            or "enable javascript" in sample
        )

    @staticmethod
    def _main_text(content: str, content_type: str | None) -> str:
        if content_type and "html" not in content_type.lower():
            return content
        soup = BeautifulSoup(content, "html.parser")
        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()
        return "\n".join(
            line.strip() for line in soup.get_text("\n").splitlines() if line.strip()
        )
