from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(slots=True)
class AgentTask:
    url: str
    goal: str
    instruction: str
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentResult:
    success: bool
    data: dict[str, Any]
    visited_urls: list[str]
    actions: list[dict[str, Any]]
    blocked: bool = False
    error: str | None = None


class AgentBackend(Protocol):
    name: str
    async def run(self, task: AgentTask) -> AgentResult: ...
