from __future__ import annotations

import os
from typing import Any
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
        success = history.is_successful() if hasattr(history, "is_successful") else bool(final)
        visited_urls = history.urls() if hasattr(history, "urls") else [task.url]
        raw_actions = history.model_actions() if hasattr(history, "model_actions") else []
        safe_urls: list[str] = []
        for visited in visited_urls:
            if not visited:
                continue
            try:
                await self.url_guard.validate(str(visited))
            except ValueError:
                continue
            safe_urls.append(str(visited))
        return AgentResult(
            success=success is True,
            data={"result": final},
            visited_urls=safe_urls,
            actions=[self._normalize_action(item) for item in raw_actions if isinstance(item, dict)],
            error=None if success is True else "agent did not complete the task successfully",
        )

    @classmethod
    def _normalize_action(cls, value: dict[str, Any]) -> dict[str, Any]:
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            normalized[key] = cls._json_value(item)
        return normalized

    @classmethod
    def _json_value(cls, value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, dict):
            return {str(k): cls._json_value(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._json_value(item) for item in value]
        for method_name in ("model_dump", "to_dict"):
            method = getattr(value, method_name, None)
            if callable(method):
                try:
                    return cls._json_value(method())
                except TypeError:
                    continue
        return str(value)
