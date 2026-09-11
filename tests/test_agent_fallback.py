from __future__ import annotations

import pytest

from argus.crawler.agent.base import AgentResult, AgentTask
from argus.crawler.agent.fallback import SequentialAgentBackend


class Backend:
    max_steps = 1

    def __init__(self, name: str, result: AgentResult, calls: list[str]) -> None:
        self.name = name
        self.result = result
        self.calls = calls

    async def run(self, task: AgentTask) -> AgentResult:
        del task
        self.calls.append(self.name)
        return self.result


def result(*, success: bool = False, blocked: bool = False) -> AgentResult:
    return AgentResult(
        success=success,
        data={},
        visited_urls=[],
        actions=[],
        blocked=blocked,
        metadata={"status": "success" if success else "failed", "reason_code": "TEST"},
    )


@pytest.mark.asyncio
async def test_agent_fallback_is_sequential_and_stops_on_success() -> None:
    calls: list[str] = []
    backend = SequentialAgentBackend(
        [
            Backend("ollama-recipe", result(), calls),
            Backend("stagehand", result(success=True), calls),
            Backend("browser-use", result(success=True), calls),
        ]
    )

    value = await backend.run(AgentTask("https://example.com", "goal", "instruction"))

    assert calls == ["ollama-recipe", "stagehand"]
    assert value.success is True
    assert value.metadata["selected_backend"] == "stagehand"
    assert value.metadata["fallback_order"] == [
        "ollama-recipe",
        "stagehand",
        "browser-use",
    ]


@pytest.mark.asyncio
async def test_access_challenge_never_triggers_backend_bypass() -> None:
    calls: list[str] = []
    backend = SequentialAgentBackend(
        [
            Backend("ollama-recipe", result(blocked=True), calls),
            Backend("stagehand", result(success=True), calls),
        ]
    )

    value = await backend.run(AgentTask("https://example.com", "goal", "instruction"))

    assert value.blocked is True
    assert calls == ["ollama-recipe"]
