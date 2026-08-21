from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from argus.contracts.models import CollectionRequest, Evidence, Observation


@dataclass(slots=True)
class SourceTask:
    source_id: str
    goal: str
    url: str
    depth: int = 0
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class SourceResult:
    observations: list[Observation]
    evidence: list[Evidence] = field(default_factory=list)
    discovered_tasks: list[SourceTask] = field(default_factory=list)
    blocked: bool = False


class SourceAdapter(Protocol):
    source_id: str
    intents: set[str]
    async def discover(self, request: CollectionRequest) -> list[SourceTask]: ...
    async def fetch(self, task: SourceTask): ...
    async def extract(self, task: SourceTask, fetched, request: CollectionRequest) -> SourceResult: ...
    async def normalize(self, result: SourceResult) -> SourceResult: ...
    async def health(self) -> dict[str, object]: ...
