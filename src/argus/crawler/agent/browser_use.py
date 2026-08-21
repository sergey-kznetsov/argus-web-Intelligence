from __future__ import annotations

import os

from argus.config import Settings
from argus.crawler.agent.base import AgentResult, AgentTask
from argus.security.urls import UrlGuard


class BrowserUseAgent:
    name = "browser-use"

    def __init__(self, settings: Settings, url_guard: UrlGuard) -> None:
        self.settings = settings
        self.url_guard = url_guard

    async def run(self, task: AgentTask) -> AgentResult:
        await self.url_guard.validate(task.url)
        try:
            from browser_use import Agent, ChatOllama
        except ImportError as exc:
            raise RuntimeError("install ARGUS with [agent-browser-use] to enable Browser Use") from exc

        os.environ.setdefault("OLLAMA_HOST", self.settings.ollama_url)
        llm = ChatOllama(model=self.settings.ollama_model)
        instruction = (
            f"Open {task.url}. {task.instruction}. Do not bypass CAPTCHAs or access controls. "
            "Return only facts visible in public sources and retain source URLs."
        )
        agent = Agent(task=instruction, llm=llm)
        history = await agent.run(max_steps=25)
        final = history.final_result() if hasattr(history, "final_result") else None
        return AgentResult(
            success=bool(final),
            data={"result": final},
            visited_urls=[task.url],
            actions=[],
            error=None if final else "agent produced no result",
        )
