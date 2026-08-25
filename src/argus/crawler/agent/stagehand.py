from __future__ import annotations

from argus.crawler.agent.base import AgentResult, AgentTask


class StagehandAgent:
    """Stable disabled boundary for a future validated local Stagehand backend."""

    name = "stagehand"

    async def run(self, task: AgentTask) -> AgentResult:
        del task
        return AgentResult(
            success=False,
            data={},
            visited_urls=[],
            actions=[],
            error="Stagehand local-LLM backend is not enabled",
            metadata={
                "backend": self.name,
                "status": "disabled",
                "reason_code": "AGENT_BACKEND_DISABLED",
            },
        )
