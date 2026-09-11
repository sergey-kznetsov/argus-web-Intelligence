from __future__ import annotations

from types import SimpleNamespace

from argus.config import Settings
from argus.crawler.agent.stagehand import StagehandAgent
from argus.security.urls import UrlGuard


def agent() -> StagehandAgent:
    return StagehandAgent(Settings(), UrlGuard.from_strings([]))


def test_stagehand_accepts_only_safe_read_only_click_hints() -> None:
    actions = agent()._safe_actions(
        [
            {"method": "click", "selector": "#more", "description": "Показать ещё"},
            {"method": "click", "selector": "#login", "description": "Войти"},
            {"method": "click", "selector": "#payment-form", "description": "Open"},
            {"method": "type", "selector": "#query", "description": "Search"},
            {"method": "click", "selector": "#more", "description": "Duplicate"},
        ]
    )

    assert actions == [{"click": {"selector": "#more"}}]


def test_stagehand_accepts_enum_like_json_schema_response_format() -> None:
    response_format = SimpleNamespace(
        type=SimpleNamespace(value="ResponseFormatType.JSON_SCHEMA"),
        schema_={"type": "object"},
    )

    assert agent()._schema(response_format) == {"type": "object"}


def test_stagehand_snapshot_cannot_load_external_resources_or_run_scripts() -> None:
    value = agent()._snapshot_url(
        '<meta http-equiv="refresh" content="0;url=https://example.net/private">'
        '<base href="https://example.net/"><script>alert(1)</script>'
        '<img src="http://127.0.0.1/private">'
        '<div style="background:url(https://example.net/tracker)">Visible</div>'
        '<a href="https://example.com/next" onclick="steal()" id="next">Next</a>'
    )

    assert value.startswith("data:text/html;charset=utf-8;base64,")
    import base64

    html = base64.b64decode(value.split(",", 1)[1]).decode("utf-8")
    assert "script" not in html
    assert "127.0.0.1" not in html
    assert "example.net" not in html
    assert "onclick" not in html
    assert "style=" not in html
    assert 'href="#"' in html
