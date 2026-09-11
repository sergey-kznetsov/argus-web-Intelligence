from __future__ import annotations

import asyncio
import ipaddress
import json
import math
import os
from contextlib import suppress
from typing import Any
from urllib.parse import urlsplit

from argus.config import Settings
from argus.crawler.agent.base import AgentResult, AgentTask
from argus.llm_health import OllamaRuntimeHealth
from argus.llm_runtime import LlmConcurrencyGate, optional_llm_ready
from argus.security.urls import UrlGuard


class BrowserUseAgent:
    """Last-resort public-web agent backed by Browser Use and local Ollama.

    The agent may discover a navigation path, but it is not itself a factual parser.
    All reusable actions still have to pass deterministic SiteRecipe compilation and
    BROWSER replay in the web adapter before they can be persisted.
    """

    name = "browser-use"
    max_steps = 25
    max_actions_per_step = 3
    max_failures = 2
    max_history_items = 6
    max_allowed_domains = 20
    max_visited_urls = 20
    max_actions = 40
    max_result_chars = 20_000
    max_error_chars = 2_000
    max_action_depth = 8
    max_action_nodes = 1_000
    max_action_string_chars = 4_000
    excluded_tools = (
        "search",
        "read_file",
        "write_file",
        "replace_file",
        "upload_file",
        "download_file",
        "open_file",
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
        self.max_steps = min(self.max_steps, int(settings.agent_max_steps))
        self.timeout_seconds = min(
            float(settings.agent_timeout_seconds),
            max(30.0, float(settings.browser_timeout_seconds) * 2.0),
            float(settings.fetch_wait_timeout_seconds),
        )
        self.step_timeout_seconds = min(60.0, self.timeout_seconds)
        self.llm_timeout_seconds = min(60.0, self.timeout_seconds)

    async def run(self, task: AgentTask) -> AgentResult:
        await self.url_guard.validate(task.url)
        if not await optional_llm_ready(self.llm_health):
            return self._failure(
                task,
                code="AGENT_LLM_UNAVAILABLE",
                message="local Ollama is unavailable",
            )
        try:
            from browser_use import Agent, Browser, ChatOllama, Tools
        except ImportError:
            if self.settings.browser_use_python is not None:
                return await self._run_isolated(task)
            return self._failure(
                task,
                code="AGENT_DEPENDENCY_UNAVAILABLE",
                message="install ARGUS with [agent-browser-use] or configure its isolated Python",
            )

        host = (urlsplit(task.url).hostname or "").casefold().strip(".")
        allowed_domains = self._allowed_domains(task, host)
        if not allowed_domains:
            return self._failure(
                task,
                code="AGENT_TARGET_OUTSIDE_ALLOWED_DOMAINS",
                message="agent target is outside the configured public-domain boundary",
            )

        # Browser Use ChatOllama reads OLLAMA_HOST. ARGUS owns this worker process and
        # deliberately binds the agent to its configured Ollama endpoint instead of an
        # unrelated inherited environment value.
        os.environ["OLLAMA_HOST"] = self.settings.ollama_url
        os.environ["ANONYMIZED_TELEMETRY"] = "false"
        llm = self._build_llm(ChatOllama)
        try:
            from browser_use import BrowserProfile
        except ImportError:
            # Compatibility for a narrow older/test adapter. The pinned production
            # profile exports BrowserProfile and takes the hardened branch below.
            browser = Browser(
                allowed_domains=allowed_domains,
                block_ip_addresses=True,
                enable_default_extensions=False,
            )
        else:
            profile = BrowserProfile(
                allowed_domains=allowed_domains,
                block_ip_addresses=True,
                enable_default_extensions=False,
                captcha_solver=False,
                accept_downloads=False,
                auto_download_pdfs=False,
                headless=True,
            )
            browser = Browser(browser_profile=profile)
        tools = Tools(exclude_actions=list(self.excluded_tools))
        instruction = (
            f"Open {task.url}. {task.instruction}. "
            "Use only public, unauthenticated pages. Do not bypass CAPTCHAs, access controls, "
            "paywalls, rate limits or robots restrictions. Do not log in, create accounts, "
            "accept terms on behalf of a user, submit forms that create/update/delete data, "
            "make purchases, upload files, download executables, or enter personal, secret or "
            "payment information. Do not use file, javascript, chrome, about or extension URLs. "
            "Stop when a CAPTCHA/access challenge is encountered. Return only a navigation "
            "trace and public source URLs. Any factual output is ignored until ARGUS fetches "
            "and verifies the source independently."
        )
        agent = Agent(
            task=instruction,
            llm=llm,
            browser=browser,
            tools=tools,
            use_vision=False,
            max_actions_per_step=self.max_actions_per_step,
            max_failures=self.max_failures,
            max_history_items=self.max_history_items,
            llm_timeout=self.llm_timeout_seconds,
            step_timeout=self.step_timeout_seconds,
            use_judge=False,
            final_response_after_failure=False,
            enable_signal_handler=False,
        )
        try:
            try:
                async with self.llm_gate.slot(self.name):
                    history = await asyncio.wait_for(
                        agent.run(max_steps=self.max_steps),
                        timeout=self.timeout_seconds,
                    )
            except TimeoutError:
                return self._failure(
                    task,
                    code="AGENT_TIMEOUT",
                    message="agent execution exceeded its bounded runtime",
                    metadata={"timeout_seconds": self.timeout_seconds},
                )

            final_raw = history.final_result() if hasattr(history, "final_result") else None
            success = (
                history.is_successful() if hasattr(history, "is_successful") else bool(final_raw)
            )
            visited_raw = history.urls() if hasattr(history, "urls") else [task.url]
            raw_actions_value = history.model_actions() if hasattr(history, "model_actions") else []
            raw_actions = (
                list(raw_actions_value) if isinstance(raw_actions_value, (list, tuple)) else []
            )
            history_errors = history.errors() if hasattr(history, "errors") else []

            safe_urls, visited_truncated = await self._safe_visited_urls(visited_raw)
            final, result_truncated = self._bounded_text(final_raw, self.max_result_chars)
            diagnostic = self._diagnostic(final, history_errors)
            blocked = any(
                marker in diagnostic.casefold()
                for marker in (
                    "captcha",
                    "verify you are human",
                    "access denied",
                    "robot check",
                    "cloudflare challenge",
                    "too many requests",
                )
            )
            if blocked:
                return AgentResult(
                    success=False,
                    data={"result": final},
                    visited_urls=safe_urls,
                    actions=[],
                    blocked=True,
                    error="public source presented an access challenge",
                    metadata=self._metadata(
                        status="blocked",
                        code="AGENT_ACCESS_CHALLENGE",
                        raw_action_count=len(raw_actions),
                        action_count=0,
                        visited_url_count=len(safe_urls),
                        visited_urls_truncated=visited_truncated,
                        result_truncated=result_truncated,
                    ),
                )

            if len(raw_actions) > self.max_actions:
                return self._failure(
                    task,
                    code="AGENT_ACTION_BUDGET_EXCEEDED",
                    message="agent produced more actions than the deterministic replay budget",
                    visited_urls=safe_urls,
                    metadata={
                        "raw_action_count": len(raw_actions),
                        "max_actions": self.max_actions,
                        "visited_urls_truncated": visited_truncated,
                    },
                )

            try:
                actions = [
                    self._normalize_action(item) for item in raw_actions if isinstance(item, dict)
                ]
            except ValueError as exc:
                return self._failure(
                    task,
                    code="AGENT_ACTION_PAYLOAD_BUDGET_EXCEEDED",
                    message=str(exc),
                    visited_urls=safe_urls,
                    metadata={"raw_action_count": len(raw_actions)},
                )

            status = "success" if success is True else "failed"
            code = "AGENT_OK" if success is True else "AGENT_INCOMPLETE"
            return AgentResult(
                success=success is True,
                data={"result": final},
                visited_urls=safe_urls,
                actions=actions,
                blocked=False,
                error=None if success is True else "agent did not complete the task successfully",
                metadata=self._metadata(
                    status=status,
                    code=code,
                    raw_action_count=len(raw_actions),
                    action_count=len(actions),
                    visited_url_count=len(safe_urls),
                    visited_urls_truncated=visited_truncated,
                    result_truncated=result_truncated,
                ),
            )
        finally:
            close = getattr(browser, "stop", None) or getattr(browser, "close", None)
            if callable(close):
                with suppress(Exception):
                    await close()

    async def _run_isolated(self, task: AgentTask) -> AgentResult:
        """Run the conflicting Browser Use dependency in its deployment-owned venv."""

        configured = self.settings.browser_use_python
        if configured is None:
            return self._failure(
                task,
                code="AGENT_DEPENDENCY_UNAVAILABLE",
                message="isolated Browser Use Python is not configured",
            )
        try:
            python = configured.resolve(strict=True)
        except OSError:
            return self._failure(
                task,
                code="AGENT_DEPENDENCY_UNAVAILABLE",
                message="isolated Browser Use Python does not exist",
            )
        if not python.is_file() or not python.is_absolute():
            return self._failure(
                task,
                code="AGENT_DEPENDENCY_UNAVAILABLE",
                message="isolated Browser Use Python must be an absolute file path",
            )

        request = json.dumps(
            {
                "url": task.url,
                "goal": task.goal,
                "instruction": task.instruction,
                "context": task.context,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        environment = os.environ.copy()
        environment["ARGUS_BROWSER_USE_PYTHON"] = ""
        process = None
        try:
            async with self.llm_gate.slot(self.name):
                process = await asyncio.create_subprocess_exec(
                    str(python),
                    "-m",
                    "argus.crawler.agent.browser_use_runner",
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=environment,
                    limit=512 * 1024,
                )
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(request),
                    timeout=self.timeout_seconds,
                )
        except TimeoutError:
            if process is not None:
                process.kill()
                with suppress(Exception):
                    await process.wait()
            return self._failure(
                task,
                code="AGENT_TIMEOUT",
                message="isolated Browser Use exceeded its bounded runtime",
            )
        except (OSError, ValueError) as exc:
            return self._failure(
                task,
                code="AGENT_RUNTIME_UNAVAILABLE",
                message="isolated Browser Use could not be started",
                metadata={"error_type": type(exc).__name__},
            )

        if process.returncode != 0:
            return self._failure(
                task,
                code="AGENT_RUNTIME_UNAVAILABLE",
                message="isolated Browser Use failed",
                metadata={
                    "subprocess_returncode": process.returncode,
                    "subprocess_stderr_present": bool(stderr),
                },
            )
        marker = "ARGUS_AGENT_RESULT="
        lines = stdout.decode("utf-8", errors="replace").splitlines()
        serialized = next(
            (line[len(marker) :] for line in reversed(lines) if line.startswith(marker)), ""
        )
        try:
            payload = json.loads(serialized)
            if not isinstance(payload, dict):
                raise ValueError("result is not an object")
            visited_urls, visited_truncated = await self._safe_visited_urls(
                payload.get("visited_urls", [])
            )
            raw_actions = payload.get("actions", [])
            if not isinstance(raw_actions, list) or len(raw_actions) > self.max_actions:
                raise ValueError("result actions exceed the bounded list contract")
            actions = [
                self._normalize_action(item) for item in raw_actions if isinstance(item, dict)
            ]
            return AgentResult(
                success=payload.get("success") is True,
                data=payload.get("data") if isinstance(payload.get("data"), dict) else {},
                visited_urls=visited_urls,
                actions=actions,
                blocked=payload.get("blocked") is True,
                error=str(payload.get("error"))[: self.max_error_chars]
                if payload.get("error")
                else None,
                metadata={
                    **(
                        payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
                    ),
                    "isolated_process": True,
                    "visited_urls_truncated": visited_truncated,
                },
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            return self._failure(
                task,
                code="AGENT_INVALID_RESULT",
                message="isolated Browser Use returned an invalid bounded result",
            )

    def _build_llm(self, chat_ollama_type):
        """Use the bounded model profile while tolerating older Browser Use releases."""

        full = {
            "model": self.settings.ollama_model,
            "host": self.settings.ollama_url,
            "timeout": self.llm_timeout_seconds,
            "ollama_options": {
                "num_ctx": self.settings.ollama_num_ctx,
                "num_predict": self.settings.ollama_num_predict,
                "num_thread": self.settings.ollama_num_thread,
                "temperature": 0.0,
                "think": False,
                "keep_alive": f"{self.settings.ollama_keep_alive_seconds}s",
            },
        }
        try:
            return chat_ollama_type(**full)
        except (TypeError, ValueError):
            # OLLAMA_HOST above still pins the endpoint, and the derived model contains
            # the fixed context/output profile created by tune-ollama-cpu.ps1.
            return chat_ollama_type(
                model=self.settings.ollama_model,
                host=self.settings.ollama_url,
            )

    async def _safe_visited_urls(self, values: Any) -> tuple[list[str], bool]:
        source = list(values) if isinstance(values, (list, tuple)) else []
        truncated = len(source) > self.max_visited_urls
        safe_urls: list[str] = []
        seen: set[str] = set()
        for visited in source[: self.max_visited_urls]:
            if not visited:
                continue
            url = str(visited)[:4_096]
            if url in seen:
                continue
            try:
                await self.url_guard.validate(url)
            except ValueError:
                continue
            seen.add(url)
            safe_urls.append(url)
        return safe_urls, truncated

    def _allowed_domains(self, task: AgentTask, host: str) -> list[str]:
        configured = task.context.get("allowed_domains", [])
        values = configured if isinstance(configured, list) else []
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in values[: self.max_allowed_domains]:
            value = str(raw).strip().casefold()
            if not value:
                continue
            if "://" in value:
                value = (urlsplit(value).hostname or "").casefold()
            value = value.strip().strip(".")
            if value.startswith("*."):
                value = value[2:]
            if not value or len(value) > 253 or "/" in value or "*" in value:
                continue
            try:
                ipaddress.ip_address(value)
            except ValueError:
                pass
            else:
                continue
            if value not in seen:
                seen.add(value)
                normalized.append(value)

        if not normalized:
            return [host] if host else []
        if not any(host == domain or host.endswith("." + domain) for domain in normalized):
            return []

        # ARGUS allowed-domain semantics include subdomains. Browser Use requires an
        # explicit wildcard pattern for that behavior, so mirror the ARGUS boundary.
        browser_patterns: list[str] = []
        for domain in normalized:
            browser_patterns.extend((domain, f"*.{domain}"))
        return browser_patterns

    def _normalize_action(self, value: dict[str, Any]) -> dict[str, Any]:
        nodes = [0]
        normalized = self._json_value(value, depth=0, nodes=nodes)
        if not isinstance(normalized, dict):
            raise ValueError("agent action did not normalize to an object")
        return normalized

    def _json_value(self, value: Any, *, depth: int, nodes: list[int]) -> Any:
        if depth > self.max_action_depth:
            raise ValueError("agent action exceeds maximum nesting depth")
        nodes[0] += 1
        if nodes[0] > self.max_action_nodes:
            raise ValueError("agent action exceeds maximum node budget")
        if value is None or isinstance(value, bool) or isinstance(value, int):
            return value
        if isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError("agent action contains a non-finite number")
            return value
        if isinstance(value, str):
            return value[: self.max_action_string_chars]
        if isinstance(value, dict):
            return {
                str(key)[:256]: self._json_value(item, depth=depth + 1, nodes=nodes)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [self._json_value(item, depth=depth + 1, nodes=nodes) for item in value]
        for method_name in ("model_dump", "to_dict"):
            method = getattr(value, method_name, None)
            if callable(method):
                try:
                    return self._json_value(method(), depth=depth + 1, nodes=nodes)
                except TypeError:
                    continue
        text, _ = self._bounded_text(value, self.max_action_string_chars)
        return text

    def _diagnostic(self, final: str, errors: Any) -> str:
        parts = [final]
        if isinstance(errors, (list, tuple)):
            for item in errors[:20]:
                if item:
                    text, _ = self._bounded_text(item, self.max_error_chars)
                    parts.append(text)
        return " ".join(parts)[: self.max_result_chars + self.max_error_chars]

    @staticmethod
    def _bounded_text(value: Any, limit: int) -> tuple[str, bool]:
        text = "" if value is None else str(value)
        return text[:limit], len(text) > limit

    def _metadata(self, *, status: str, code: str, **extra: Any) -> dict[str, Any]:
        return {
            "backend": self.name,
            "status": status,
            "reason_code": code,
            "max_steps": self.max_steps,
            "max_actions_per_step": self.max_actions_per_step,
            "max_failures": self.max_failures,
            "max_history_items": self.max_history_items,
            "timeout_seconds": self.timeout_seconds,
            "llm_timeout_seconds": self.llm_timeout_seconds,
            "step_timeout_seconds": self.step_timeout_seconds,
            "max_actions": self.max_actions,
            "max_visited_urls": self.max_visited_urls,
            "excluded_tools": list(self.excluded_tools),
            **extra,
        }

    def _failure(
        self,
        task: AgentTask,
        *,
        code: str,
        message: str,
        visited_urls: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AgentResult:
        del task
        return AgentResult(
            success=False,
            data={},
            visited_urls=visited_urls or [],
            actions=[],
            blocked=False,
            error=message[: self.max_error_chars],
            metadata=self._metadata(status="failed", code=code, **(metadata or {})),
        )