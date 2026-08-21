from __future__ import annotations

from argus.crawler.agent.base import AgentResult, AgentTask


class StagehandAgent:
    """Stable boundary for Stagehand integration.

    Crawlee provides StagehandCrawler, but local-model configuration can evolve independently.
    Milestone 1 keeps the core independent and fails explicitly until a validated local LLM client
    is configured.
    """

    name = "stagehand"

    async def run(self, task: AgentTask) -> AgentResult:
        del task
        return AgentResult(
            success=False,
            data={},
            visited_urls=[],
            actions=[],
            error="Stagehand local-LLM backend is not enabled in milestone 1",
        )
