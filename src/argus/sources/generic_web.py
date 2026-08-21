from __future__ import annotations

from urllib.parse import urlparse

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

    def __init__(self, fast: FastCrawlerRuntime, browser: BrowserCrawlerRuntime,
                 snapshots: SnapshotService) -> None:
        self.fast = fast
        self.browser = browser
        self.snapshots = snapshots

    async def discover(self, request: CollectionRequest) -> list[SourceTask]:
        tasks: list[SourceTask] = []
        for url in request.constraints.seed_urls:
            tasks.append(SourceTask(source_id=self.source_id, goal=request.intents[0], url=str(url), depth=0))
        return tasks

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
        snapshot = await self.snapshots.capture(self.source_id, fetched.final_url, fetched.text, fetched.content_type)
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
            source=EvidenceSource(provider=self.source_id, url=fetched.final_url,
                                  collected_at=observation.collected_at, source_id=self.source_id),
        )
        discovered: list[SourceTask] = []
        max_depth = request.constraints.max_depth
        if task.depth < max_depth:
            allowed = {d.lower() for d in request.constraints.allowed_domains}
            denied = {d.lower() for d in request.constraints.denied_domains}
            for link in fetched.links[: request.constraints.max_pages]:
                domain = (urlparse(link).hostname or "").lower()
                if denied and any(domain == d or domain.endswith("." + d) for d in denied):
                    continue
                if allowed and not any(domain == d or domain.endswith("." + d) for d in allowed):
                    continue
                discovered.append(SourceTask(
                    source_id=self.source_id,
                    goal=task.goal,
                    url=link,
                    depth=task.depth + 1,
                    metadata={"collection_id": observation.collection_id},
                ))
        return SourceResult(observations=[observation], evidence=[evidence], discovered_tasks=discovered)

    async def normalize(self, result: SourceResult) -> SourceResult:
        return result

    async def health(self) -> dict[str, object]:
        return {"source_id": self.source_id, "status": "ok"}

    @staticmethod
    def _needs_browser(html: str) -> bool:
        sample = html[:100_000].lower()
        return "__next_data__" in sample or "id=\"root\"></div>" in sample or "enable javascript" in sample

    @staticmethod
    def _main_text(content: str, content_type: str | None) -> str:
        if content_type and "html" not in content_type.lower():
            return content
        soup = BeautifulSoup(content, "html.parser")
        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()
        return "\n".join(line.strip() for line in soup.get_text("\n").splitlines() if line.strip())
