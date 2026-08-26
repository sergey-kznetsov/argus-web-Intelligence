from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup, Tag

from argus.config import Settings
from argus.crawler.agent.base import AgentResult, AgentTask
from argus.security.urls import UrlGuard


@dataclass(slots=True)
class _Control:
    control_id: int
    kind: str
    label: str
    selector: str | None = None
    url: str | None = None


class OllamaRecipeAgent:
    """Plan bounded deterministic public-page interactions with local Ollama.

    The model never controls Playwright directly and its text is never Evidence. It may
    only choose from controls extracted from an already fetched public page. ARGUS then
    compiles the selected controls into a SiteRecipe and verifies that recipe through
    the normal BROWSER runtime before any resulting page can be extracted.
    """

    name = "ollama-recipe"
    max_steps = 1
    max_actions = 6
    max_visited_urls = 8
    max_controls = 120
    max_page_chars = 300_000
    max_label_chars = 300
    max_prompt_chars = 40_000
    max_scroll_pixels = 8_000

    _DENIED_LABEL_MARKERS = (
        "login",
        "log in",
        "sign in",
        "register",
        "signup",
        "sign up",
        "войти",
        "регистрац",
        "купить",
        "оплат",
        "checkout",
        "cart",
        "корзин",
        "delete",
        "remove",
        "удалить",
        "upload",
        "загрузить файл",
        "download",
        "скачать",
        "subscribe",
        "подписаться",
    )

    def __init__(self, settings: Settings, url_guard: UrlGuard) -> None:
        self.settings = settings
        self.url_guard = url_guard
        self.timeout_seconds = min(30.0, float(settings.fetch_wait_timeout_seconds))

    async def run(self, task: AgentTask) -> AgentResult:
        await self.url_guard.validate(task.url)
        page_html = str(task.context.get("page_html") or "")
        page_url = str(task.context.get("page_url") or task.url)
        if not page_html.strip():
            return self._failure(
                "AGENT_PAGE_CONTEXT_REQUIRED",
                "native recipe agent requires an already fetched public page",
                retryable=False,
            )

        controls = await self._controls(
            page_html[: self.max_page_chars],
            page_url=page_url,
            allowed_domains=task.context.get("allowed_domains", []),
        )
        if not controls:
            return self._failure(
                "AGENT_NO_SAFE_CONTROLS",
                "page exposes no bounded safe controls for deterministic replay",
                retryable=False,
            )

        prompt = self._prompt(task, controls)
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds, trust_env=False) as client:
                response = await client.post(
                    f"{self.settings.ollama_url.rstrip('/')}/api/generate",
                    json={
                        "model": self.settings.ollama_model,
                        "prompt": prompt,
                        "stream": False,
                        "format": "json",
                    },
                )
                response.raise_for_status()
                payload = response.json()
                raw = payload.get("response", "{}")
                plan = json.loads(raw)
        except (httpx.HTTPError, ValueError, TypeError, json.JSONDecodeError) as exc:
            return self._failure(
                "AGENT_LLM_UNAVAILABLE",
                "local Ollama recipe planning failed",
                retryable=True,
                error_type=type(exc).__name__,
            )

        actions = self._actions_from_plan(plan, controls)
        if not actions:
            return self._failure(
                "AGENT_NO_SAFE_PLAN",
                "local model did not select a safe deterministic interaction plan",
                retryable=False,
            )
        return AgentResult(
            success=True,
            data={"planner": self.name},
            visited_urls=[page_url],
            actions=actions,
            metadata={
                "status": "success",
                "code": "AGENT_OK",
                "backend": self.name,
                "control_count": len(controls),
                "action_count": len(actions),
                "agent_output_is_evidence": False,
                "deterministic_replay_required": True,
            },
        )

    async def _controls(
        self,
        html: str,
        *,
        page_url: str,
        allowed_domains: object,
    ) -> list[_Control]:
        soup = BeautifulSoup(html, "html.parser")
        controls: list[_Control] = []
        seen: set[tuple[str, str]] = set()
        domains = self._domains(allowed_domains, page_url)

        for element in soup.find_all(["a", "button"]):
            if len(controls) >= self.max_controls:
                break
            if not isinstance(element, Tag):
                continue
            label = self._label(element)
            if not label or self._denied_label(label):
                continue

            if element.name == "a":
                href = str(element.get("href") or "").strip()
                if not href:
                    continue
                url = urljoin(page_url, href)
                if not self._same_domain_boundary(url, domains):
                    continue
                try:
                    await self.url_guard.validate(url)
                except ValueError:
                    continue
                key = ("goto", url)
                if key in seen:
                    continue
                seen.add(key)
                controls.append(
                    _Control(
                        control_id=len(controls) + 1,
                        kind="goto",
                        label=label,
                        url=url,
                    )
                )
                continue

            selector = self._selector(element)
            if not selector:
                continue
            key = ("click", selector)
            if key in seen:
                continue
            seen.add(key)
            controls.append(
                _Control(
                    control_id=len(controls) + 1,
                    kind="click",
                    label=label,
                    selector=selector,
                )
            )

        for element in soup.find_all(attrs={"role": ["tab", "button"]}):
            if len(controls) >= self.max_controls:
                break
            if not isinstance(element, Tag) or element.name in {"a", "button"}:
                continue
            label = self._label(element)
            if not label or self._denied_label(label):
                continue
            selector = self._selector(element)
            if not selector:
                continue
            key = ("click", selector)
            if key in seen:
                continue
            seen.add(key)
            controls.append(
                _Control(
                    control_id=len(controls) + 1,
                    kind="click",
                    label=label,
                    selector=selector,
                )
            )
        return controls

    def _prompt(self, task: AgentTask, controls: list[_Control]) -> str:
        compact = [
            {
                "id": item.control_id,
                "kind": item.kind,
                "label": item.label,
                "url": item.url,
            }
            for item in controls
        ]
        prompt = (
            "You are ARGUS deterministic interaction planner. Do not answer the research "
            "question and do not invent selectors, URLs or facts. Choose only from the "
            "supplied public-page controls. The goal is to reveal the requested factual "
            "content such as reviews, comments, details or expandable public sections. "
            "Never choose login, registration, purchase, checkout, upload, download, "
            "delete, subscription, CAPTCHA/access-control or form-submission actions. "
            "Return strict JSON: {\"actions\":[{\"control_id\":N}, "
            "{\"scroll_pixels\":1200}]}. Use at most 6 actions. A scroll may be used "
            "after a chosen tab/control to reveal lazy-loaded public content. If no safe "
            "control is useful, return {\"actions\":[]}. "
            f"Goal: {task.goal}. Instruction: {task.instruction}. "
            f"Controls: {json.dumps(compact, ensure_ascii=False)}"
        )
        return prompt[: self.max_prompt_chars]

    def _actions_from_plan(
        self,
        plan: object,
        controls: list[_Control],
    ) -> list[dict[str, object]]:
        if not isinstance(plan, dict):
            return []
        raw_actions = plan.get("actions")
        if not isinstance(raw_actions, list):
            return []
        by_id = {item.control_id: item for item in controls}
        actions: list[dict[str, object]] = []
        used_controls: set[int] = set()

        for raw in raw_actions[: self.max_actions]:
            if not isinstance(raw, dict):
                continue
            if "control_id" in raw:
                try:
                    control_id = int(raw["control_id"])
                except (TypeError, ValueError):
                    continue
                if control_id in used_controls:
                    continue
                control = by_id.get(control_id)
                if control is None:
                    continue
                used_controls.add(control_id)
                if control.kind == "goto" and control.url:
                    actions.append({"go_to_url": {"url": control.url}})
                elif control.kind == "click" and control.selector:
                    actions.append({"click": {"selector": control.selector}})
                continue

            if "scroll_pixels" in raw:
                try:
                    pixels = int(raw["scroll_pixels"])
                except (TypeError, ValueError):
                    continue
                pixels = max(-self.max_scroll_pixels, min(self.max_scroll_pixels, pixels))
                if pixels:
                    actions.append({"scroll": {"pixels": pixels}})
        return actions[: self.max_actions]

    def _selector(self, element: Tag) -> str | None:
        tag = str(element.name or "*").lower()
        for key in ("data-testid", "data-test", "id", "name", "aria-label"):
            value = element.get(key)
            if isinstance(value, str) and value.strip():
                return f'{tag}[{key}={self._css_string(value.strip())}]'
        label = self._label(element)
        if label and len(label) <= 120:
            return f'{tag}:has-text({self._css_string(label)})'
        return None

    def _label(self, element: Tag) -> str:
        for key in ("aria-label", "title"):
            value = element.get(key)
            if isinstance(value, str) and value.strip():
                return " ".join(value.split())[: self.max_label_chars]
        text = " ".join(element.get_text(" ", strip=True).split())
        return text[: self.max_label_chars]

    def _denied_label(self, label: str) -> bool:
        normalized = label.casefold()
        return any(marker in normalized for marker in self._DENIED_LABEL_MARKERS)

    @staticmethod
    def _domains(raw: object, page_url: str) -> list[str]:
        values = raw if isinstance(raw, list) else []
        result: list[str] = []
        for value in values:
            candidate = str(value).strip().casefold().strip(".")
            if "://" in candidate:
                candidate = (urlsplit(candidate).hostname or "").casefold().strip(".")
            if candidate and "/" not in candidate and "*" not in candidate:
                result.append(candidate)
        if result:
            return sorted(set(result))
        host = (urlsplit(page_url).hostname or "").casefold().strip(".")
        return [host] if host else []

    @staticmethod
    def _same_domain_boundary(url: str, domains: list[str]) -> bool:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        host = parsed.hostname.casefold().strip(".")
        return any(host == domain or host.endswith("." + domain) for domain in domains)

    @staticmethod
    def _css_string(value: str) -> str:
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'

    def _failure(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
        error_type: str | None = None,
    ) -> AgentResult:
        metadata: dict[str, object] = {
            "status": "failed",
            "code": code,
            "backend": self.name,
            "retryable": retryable,
            "agent_output_is_evidence": False,
        }
        if error_type:
            metadata["error_type"] = error_type
        return AgentResult(
            success=False,
            data={},
            visited_urls=[],
            actions=[],
            error=message,
            metadata=metadata,
        )
