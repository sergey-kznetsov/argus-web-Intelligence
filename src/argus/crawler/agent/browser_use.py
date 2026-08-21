from __future__ import annotations

import os
from urllib.parse import urlsplit

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
            from browser_use import Agent, Browser, ChatOllama
        except ImportError as exc:
            raise RuntimeError("install ARGUS with [agent-browser-use] to enable Browser Use") from exc

        host = (urlsplit(task.url).hostname or "").lower()
        configured = task.context.get("allowed_domains", [])
        allowed_domains = [str(item) for item in configured if str(item).strip()]
        if not allowed_domains:
            allowed_domains = [host]

        os.environ.setdefault("OLLAMA_HOST", self.settings.ollama_url)
        os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")
        llm = ChatOllama(model=self.settings.ollama_model)
        browser = Browser(
            allowed_domains=allowed_domains,
            block_ip_addresses=True,
            enable_default_extensions=False,
        )
        instruction = (
            f"Open {task.url}. {task.instruction}. Do not bypass CAPTCHAs or access controls. "
            "Do not log in or submit sensitive information. Return only facts visible in public "
            "sources and retain source URLs."
        )
        agent = Agent(task=instruction, llm=llm, browser=browser)
        history = await agent.run(max_steps=25)
        final = history.final_result() if hasattr(history, "final_result") else None
        visited_urls = history.urls() if hasattr(history, "urls") else [task.url]
        safe_urls: list[str] = []
        for visited in visited_urls:
            try:
                await self.url_guard.validate(str(visited))
            except ValueError:
                continue
            safe_urls.append(str(visited))
        return AgentResult(
            success=bool(final),
            data={"result": final},
            visited_urls=safe_urls,
            actions=[],
            error=None if final else "agent produced no result",
        )
