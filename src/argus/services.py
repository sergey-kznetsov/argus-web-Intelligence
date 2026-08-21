from __future__ import annotations

import asyncio
from dataclasses import dataclass

from argus.crawler.browser.runtime import BrowserCrawlerRuntime
from argus.crawler.fast.runtime import FastCrawlerRuntime
from argus.orchestrator.service import CollectionOrchestrator
from argus.sources.registry import SourceRegistry
from argus.storage.base import Repository


@dataclass(slots=True)
class ServiceContainer:
    """Own long-lived ARGUS services and shut them down in dependency order."""

    repository: Repository
    registry: SourceRegistry
    orchestrator: CollectionOrchestrator
    fast: FastCrawlerRuntime
    browser: BrowserCrawlerRuntime

    async def start(self) -> None:
        await self.orchestrator.start()

    async def shutdown(self) -> None:
        # Stop collection jobs first so no caller can enqueue new crawler requests during shutdown.
        await self.orchestrator.shutdown()
        # FAST and BROWSER do not depend on each other, so they can drain concurrently.
        await asyncio.gather(
            self.browser.shutdown(),
            self.fast.shutdown(),
            return_exceptions=False,
        )
