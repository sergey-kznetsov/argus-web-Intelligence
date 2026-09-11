from __future__ import annotations

from typing import Any

from argus.recipes.models import RecipeStep


class AgentRecipeCompiler:
    """Compile a conservative subset of agent actions into deterministic recipe steps."""

    _CLICK_ACTIONS = {"click", "click_element", "click_element_by_index"}
    _FILL_ACTIONS = {"input_text", "fill", "type_text"}
    _SELECT_ACTIONS = {"select", "select_option"}
    _NAVIGATE_ACTIONS = {"go_to_url", "navigate", "open_url"}
    _DENIED_INTERACTION_MARKERS = (
        "password",
        "passwd",
        "login",
        "log in",
        "sign in",
        "sign up",
        "register",
        "captcha",
        "checkout",
        "payment",
        "credit card",
        "purchase",
        "subscribe",
        "delete",
        "upload",
        "download",
        "send",
        "save",
        "confirm",
        "войти",
        "парол",
        "регистрац",
        "капч",
        "оплат",
        "купить",
        "удал",
        "загруз",
        "скач",
        "отправ",
        "сохран",
        "подтверд",
    )
    _SAFE_KEYS = frozenset(
        {
            "Enter",
            "Escape",
            "ArrowUp",
            "ArrowDown",
            "ArrowLeft",
            "ArrowRight",
            "PageUp",
            "PageDown",
            "Home",
            "End",
            "Tab",
        }
    )

    def compile(self, actions: list[dict[str, Any]]) -> list[RecipeStep] | None:
        steps: list[RecipeStep] = []
        for raw in actions:
            action_name, params = self._action(raw)
            if not action_name:
                continue
            if self._denied_interaction(raw, action_name, params):
                return None
            if action_name in {"done", "extract_content"}:
                continue
            if action_name in self._NAVIGATE_ACTIONS:
                url = self._string(params, "url")
                if not url:
                    return None
                steps.append(RecipeStep(action="goto", value=url))
                continue
            if action_name in self._CLICK_ACTIONS:
                selector = self._selector(raw) or self._string(params, "selector")
                if not selector:
                    return None
                steps.append(RecipeStep(action="click", selector=selector))
                continue
            if action_name in self._FILL_ACTIONS:
                selector = self._selector(raw) or self._string(params, "selector")
                text = self._string(params, "text", "value")
                if not selector or text is None:
                    return None
                steps.append(RecipeStep(action="fill", selector=selector, value=text))
                continue
            if action_name in self._SELECT_ACTIONS:
                selector = self._selector(raw) or self._string(params, "selector")
                value = self._string(params, "value")
                if not selector or value is None:
                    return None
                steps.append(RecipeStep(action="select", selector=selector, value=value))
                continue
            if action_name in {"send_keys", "press"}:
                selector = self._selector(raw) or self._string(params, "selector")
                keys = self._string(params, "keys", "key", "value")
                if not selector or keys not in self._SAFE_KEYS:
                    return None
                steps.append(RecipeStep(action="press", selector=selector, value=keys))
                continue
            if action_name in {"scroll", "scroll_down", "scroll_up"}:
                pixels = params.get("pixels") or params.get("amount") or 1200
                try:
                    pixels = int(pixels)
                except (TypeError, ValueError):
                    pixels = 1200
                if action_name == "scroll_up" and pixels > 0:
                    pixels = -pixels
                steps.append(RecipeStep(action="scroll", data={"pixels": pixels}))
                continue
            # Do not silently omit a required action from a persisted deterministic path.
            return None
        return steps or None

    @classmethod
    def _denied_interaction(
        cls,
        raw: dict[str, Any],
        action_name: str,
        params: dict[str, Any],
    ) -> bool:
        if action_name in {"done", "extract_content", "scroll", "scroll_down", "scroll_up"}:
            return False
        values = [action_name]
        values.extend(cls._bounded_strings(params))
        values.extend(cls._bounded_strings(raw.get("interacted_element")))
        haystack = " ".join(values).casefold()[:20_000]
        return any(marker in haystack for marker in cls._DENIED_INTERACTION_MARKERS)

    @classmethod
    def _bounded_strings(cls, value: Any, *, depth: int = 0) -> list[str]:
        if depth > 5:
            return []
        if isinstance(value, str):
            return [value[:2_000]]
        if isinstance(value, dict):
            result: list[str] = []
            for key, item in list(value.items())[:100]:
                result.append(str(key)[:256])
                result.extend(cls._bounded_strings(item, depth=depth + 1))
            return result
        if isinstance(value, (list, tuple)):
            result = []
            for item in value[:100]:
                result.extend(cls._bounded_strings(item, depth=depth + 1))
            return result
        return []

    @staticmethod
    def _action(raw: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
        ignored = {"interacted_element", "result"}
        names = [key for key in raw if key not in ignored]
        if not names:
            return None, {}
        if len(names) != 1:
            return "__invalid_multiple_actions__", {}
        name = names[0]
        params = raw.get(name)
        return name, params if isinstance(params, dict) else {}

    @classmethod
    def _selector(cls, raw: dict[str, Any]) -> str | None:
        element = cls._element_dict(raw.get("interacted_element"))
        if not element:
            return None
        attrs = element.get("attributes") if isinstance(element.get("attributes"), dict) else {}
        node = str(element.get("node_name") or "*").lower()
        for key in ("data-testid", "data-test", "id", "name", "aria-label"):
            value = attrs.get(key)
            if isinstance(value, str) and value:
                return f'{node}[{key}={cls._css_string(value)}]'
        xpath = element.get("x_path") or element.get("xpath")
        if isinstance(xpath, str) and xpath.strip():
            normalized = xpath.strip()
            if not normalized.startswith(("/", "(")):
                normalized = "//" + normalized
            return "xpath=" + normalized
        return None

    @staticmethod
    def _element_dict(element: Any) -> dict[str, Any]:
        if isinstance(element, dict):
            return element
        if element is None:
            return {}
        for method_name in ("model_dump", "to_dict"):
            method = getattr(element, method_name, None)
            if callable(method):
                data = method()
                if isinstance(data, dict):
                    return data
        return {}

    @staticmethod
    def _string(params: dict[str, Any], *keys: str) -> str | None:
        for key in keys:
            value = params.get(key)
            if value is not None:
                return str(value)
        return None

    @staticmethod
    def _css_string(value: str) -> str:
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'