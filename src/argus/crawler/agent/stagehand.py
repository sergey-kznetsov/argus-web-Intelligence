from __future__ import annotations

import asyncio
import base64
import json
from contextlib import suppress
from typing import Any

import httpx
from bs4 import BeautifulSoup

from argus.config import Settings
from argus.crawler.agent.base import AgentResult, AgentTask
from argus.llm_health import OllamaRuntimeHealth
from argus.llm_runtime import LlmConcurrencyGate, optional_llm_ready
from argus.security.urls import UrlGuard


class StagehandAgent:
    """Return bounded Stagehand navigation hints for deterministic Playwright replay.

    Stagehand does not extract facts for ARGUS and does not execute the proposed action.
    It observes one already validated public page, returns safe selectors, and the normal
    SiteRecipe lifecycle performs and verifies the interaction independently.
    """

    name = "stagehand"
    max_steps = 1
    max_actions = 6
    max_selector_chars = 2_000
    max_page_chars = 300_000
    _DENIED_MARKERS = (
        "login",
        "sign in",
        "register",
        "password",
        "captcha",
        "paywall",
        "payment",
        "pay now",
        "checkout",
        "purchase",
        "delete",
        "upload",
        "download",
        "subscribe",
        "submit",
        "send",
        "save",
        "confirm",
        "войти",
        "регистрац",
        "парол",
        "оплат",
        "платеж",
        "платёж",
        "купить",
        "удал",
        "загруз",
        "скач",
        "отправ",
        "сохран",
        "подтверд",
    )

    def __init__(
        self,
        settings: Settings,
        url_guard: UrlGuard,
        *,
        llm_gate: LlmConcurrencyGate | None = None,
        llm_health: OllamaRuntimeHealth | None = None,
    ) -> None:
        self.settings = settings
        self.url_guard = url_guard
        self.llm_gate = llm_gate or LlmConcurrencyGate(settings.llm_max_concurrency)
        self.llm_health = llm_health
        self.timeout_seconds = min(
            float(settings.agent_timeout_seconds),
            float(settings.fetch_wait_timeout_seconds),
        )

    async def run(self, task: AgentTask) -> AgentResult:
        await self.url_guard.validate(task.url)
        if not str(task.context.get("page_html") or "").strip():
            return self._failure(
                "AGENT_PAGE_CONTEXT_REQUIRED",
                "Stagehand requires an already fetched public-page snapshot",
            )
        if not await optional_llm_ready(self.llm_health):
            return self._failure("AGENT_LLM_UNAVAILABLE", "local Ollama is unavailable")

        try:
            from stagehand import LLMStructuredGenerateResult, Stagehand, local_browser
        except ImportError:
            return self._failure(
                "AGENT_DEPENDENCY_UNAVAILABLE",
                "install ARGUS with [stagehand] to enable Stagehand",
            )

        async def generate(params):
            return await self._generate(params, LLMStructuredGenerateResult)

        try:
            async with self.llm_gate.slot(self.name):
                return await asyncio.wait_for(
                    self._observe(task, Stagehand, local_browser, generate),
                    timeout=self.timeout_seconds,
                )
        except TimeoutError:
            return self._failure("AGENT_TIMEOUT", "Stagehand exceeded its bounded runtime")
        except Exception as exc:
            return self._failure(
                "AGENT_RUNTIME_UNAVAILABLE",
                "Stagehand local navigation planning failed",
                error_type=type(exc).__name__,
            )

    async def _observe(
        self, task: AgentTask, stagehand_type, local_browser, generate
    ) -> AgentResult:
        browser = None
        stagehand = None
        try:
            browser = await local_browser.launch(headless=True, accept_downloads=False)
            stagehand = await stagehand_type.create(browser=browser, model=generate)
            pages = await browser.context.pages()
            if not pages:
                return self._failure("AGENT_NO_PAGE", "Stagehand did not create a browser page")
            await pages[0].goto(self._snapshot_url(str(task.context["page_html"])))
            instruction = (
                f"Find only public read-only navigation controls needed to {task.instruction}. "
                "Do not find login, registration, payment, upload, download, CAPTCHA, "
                "subscription, submit, save, delete or other state-changing controls."
            )
            observed = await stagehand.observe(
                instruction,
                page=pages[0],
                timeout=int(self.timeout_seconds * 1_000),
                cache=False,
            )
            actions = self._safe_actions(getattr(observed, "data", []))
            if not actions:
                return self._failure(
                    "AGENT_NO_SAFE_PLAN",
                    "Stagehand found no safe deterministic navigation action",
                )
            return AgentResult(
                success=True,
                data={"planner": self.name},
                visited_urls=[task.url],
                actions=actions,
                metadata={
                    "backend": self.name,
                    "status": "success",
                    "reason_code": "AGENT_OK",
                    "action_count": len(actions),
                    "max_actions": self.max_actions,
                    "navigation_hints_only": True,
                    "agent_output_is_evidence": False,
                    "deterministic_replay_required": True,
                    "ollama_openai_base_url": f"{self.settings.ollama_url.rstrip('/')}/v1",
                },
            )
        finally:
            if stagehand is not None:
                with suppress(Exception):
                    await stagehand.close()
            if browser is not None:
                with suppress(Exception):
                    await browser.close()

    async def _generate(self, params: Any, result_type: Any) -> Any:
        response_format = getattr(params, "response_format", None)
        schema = self._schema(response_format)
        if schema is None:
            raise TypeError("Stagehand requested an unsupported non-structured generation")

        messages: list[dict[str, str]] = []
        system_prompt = str(getattr(params, "system_prompt", "") or "").strip()
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        for message in list(getattr(params, "messages", []) or []):
            role_value = getattr(getattr(message, "role", None), "value", None)
            role = str(role_value or getattr(message, "role", "user"))
            content = self._message_text(getattr(message, "content", ""))
            if content:
                messages.append({"role": role, "content": content})

        name = str(getattr(response_format, "name", "stagehand_action") or "stagehand_action")
        payload = {
            "model": self.settings.ollama_model,
            "messages": messages,
            "stream": False,
            "temperature": 0,
            "max_tokens": self.settings.ollama_num_predict,
            "reasoning_effort": "none",
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": name, "schema": schema, "strict": True},
            },
        }
        timeout = httpx.Timeout(self.settings.llm_request_timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            response = await client.post(
                f"{self.settings.ollama_url.rstrip('/')}/v1/chat/completions",
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
        choices = body.get("choices") if isinstance(body, dict) else None
        if not isinstance(choices, list) or not choices:
            raise ValueError("Ollama returned no Stagehand completion")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        text = str(message.get("content") or "") if isinstance(message, dict) else ""
        structured = json.loads(text)
        return result_type.model_validate(
            {
                "role": "assistant",
                "content": {"type": "text", "text": text},
                "output_format": "json_schema",
                "structured_content": structured,
            }
        )

    @staticmethod
    def _schema(response_format: Any) -> dict[str, object] | None:
        if response_format is None:
            return None
        kind = getattr(response_format, "type", None)
        kind = getattr(kind, "value", kind)
        if not str(kind).casefold().endswith("json_schema"):
            return None
        value = getattr(response_format, "schema_", None)
        if value is None:
            value = getattr(response_format, "schema", None)
        if hasattr(value, "model_dump"):
            value = value.model_dump(mode="json")
        return value if isinstance(value, dict) else None

    @staticmethod
    def _message_text(content: Any) -> str:
        blocks = content if isinstance(content, list) else [content]
        parts: list[str] = []
        for block in blocks:
            root = getattr(block, "root", block)
            kind = getattr(root, "type", None)
            kind = getattr(kind, "value", kind)
            if kind == "text" or isinstance(root, str):
                value = root if isinstance(root, str) else getattr(root, "text", "")
                if value:
                    parts.append(str(value))
        return "\n".join(parts)

    def _safe_actions(self, values: Any) -> list[dict[str, object]]:
        if not isinstance(values, (list, tuple)):
            return []
        actions: list[dict[str, object]] = []
        seen: set[str] = set()
        for value in values:
            method = str(self._field(value, "method") or "").strip().casefold()
            selector = str(self._field(value, "selector") or "").strip()
            description = str(self._field(value, "description") or "").strip()
            if method != "click" or not selector or len(selector) > self.max_selector_chars:
                continue
            safety_text = f"{description} {selector}".casefold()
            if any(marker in safety_text for marker in self._DENIED_MARKERS):
                continue
            if selector in seen:
                continue
            seen.add(selector)
            actions.append({"click": {"selector": selector}})
            if len(actions) >= self.max_actions:
                break
        return actions

    def _snapshot_url(self, html: str) -> str:
        """Create an inert same-process DOM snapshot with no external subrequests."""

        soup = BeautifulSoup(html[: self.max_page_chars], "html.parser")
        for element in soup.find_all(
            ["script", "style", "iframe", "object", "embed", "link", "meta", "base"]
        ):
            element.decompose()
        for element in soup.find_all(True):
            for attribute in list(element.attrs):
                normalized = str(attribute).casefold()
                if normalized.startswith("on") or normalized in {
                    "src",
                    "srcset",
                    "srcdoc",
                    "action",
                    "formaction",
                    "poster",
                    "style",
                    "xlink:href",
                }:
                    del element.attrs[attribute]
            if element.has_attr("href"):
                element.attrs["href"] = "#"
        encoded = base64.b64encode(str(soup).encode("utf-8")).decode("ascii")
        return f"data:text/html;charset=utf-8;base64,{encoded}"

    @staticmethod
    def _field(value: Any, name: str) -> Any:
        if isinstance(value, dict):
            return value.get(name)
        return getattr(value, name, None)

    def _failure(self, code: str, message: str, **metadata: object) -> AgentResult:
        return AgentResult(
            success=False,
            data={},
            visited_urls=[],
            actions=[],
            error=message,
            metadata={
                "backend": self.name,
                "status": "failed",
                "reason_code": code,
                "agent_output_is_evidence": False,
                **metadata,
            },
        )