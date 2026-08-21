from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from argus.contracts.models import CollectionRequest, Evidence, Observation, StructuredError


@dataclass(slots=True)
class SourceTask:
    source_id: str
    goal: str
    url: str
    depth: int = 0
    metadata: dict[str, object] = field(default_factory=dict)
    task_key: str | None = None

    @property
    def dedupe_key(self) -> str:
        """Stable per-collection task identity.

        URL-only identity remains the backward-compatible default for ordinary GET
        crawling. Providers that execute distinct queries against one endpoint can
        provide an explicit task_key so checkpoint/dedupe logic does not collapse them.
        """

        return self.task_key or f"{self.source_id}:{self.url}"


@dataclass(slots=True)
class SourceResult:
    observations: list[Observation]
    evidence: list[Evidence] = field(default_factory=list)
    discovered_tasks: list[SourceTask] = field(default_factory=list)
    blocked: bool = False
    partial: bool = False
    errors: list[StructuredError] = field(default_factory=list)


class SourceAdapter(Protocol):
    source_id: str
    intents: set[str]

    async def discover(self, request: CollectionRequest) -> list[SourceTask]: ...

    async def fetch(self, task: SourceTask): ...

    async def extract(
        self,
        task: SourceTask,
        fetched,
        request: CollectionRequest,
    ) -> SourceResult: ...

    async def normalize(self, result: SourceResult) -> SourceResult: ...

    async def health(self) -> dict[str, object]: ...
