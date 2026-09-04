from __future__ import annotations

import pytest

from argus.config import Settings
from argus.contracts.models import CollectionRequest
from argus.crawler.block_detection import looks_like_blocked_page
from argus.research.planner import HeuristicResearchPlanner
from argus.sources.web_content import extract_readable_text


def test_login_and_navigation_shell_is_not_document_text() -> None:
    html = """
    <html><body>
      <header>Марковские форумы</header>
      <nav>Главная Пользователи Помощь</nav>
      <form><label>Логин</label><input><label>Пароль</label><input></form>
      <main><article><p>На Пушкинской у дома 277 не работает фонарь.</p></article></main>
      <footer>Архив Правила</footer>
    </body></html>
    """

    text = extract_readable_text(html, "text/html; charset=utf-8")

    assert "не работает фонарь" in text
    assert "Логин" not in text
    assert "Пароль" not in text
    assert "Главная" not in text
    assert "Архив Правила" not in text


def test_short_checking_shell_is_blocked_but_normal_article_is_not() -> None:
    assert looks_like_blocked_page(
        "<html><title>Just a moment...</title><body>Checking your browser...</body></html>",
        "text/html",
    )
    assert looks_like_blocked_page("Checking...", "text/html")
    assert not looks_like_blocked_page(
        "Редакция проверяет документы. Жители сообщили о проблемах с освещением " * 100,
        "text/html",
    )


@pytest.mark.asyncio
async def test_default_urban_signal_budget_keeps_three_public_map_queries() -> None:
    assert Settings.model_fields["discovery_max_queries"].default == 12
    request = CollectionRequest(
        consumer="kraken.development.uds",
        consumer_profile_version=1,
        capability="urban_signals",
        analysis_id="kraken-map-budget",
        territory={
            "city": "Ижевск",
            "address": "Ижевск, улица Пушкинская, дом 277",
            "metadata": {"street": "Пушкинская", "house": "277"},
        },
        intents=[
            "comments",
            "discussions",
            "complaints",
            "incidents",
            "posts",
            "public_appeals",
            "resident_messages",
            "local_news",
        ],
    )

    plan = await HeuristicResearchPlanner(max_queries=12).plan(request)
    joined = "\n".join(plan.queries)

    assert 'site:yandex.ru/maps "Ижевск, Пушкинская"' in joined
    assert 'site:2gis.ru "Ижевск, Пушкинская"' in joined
    assert 'site:google.com/maps "Ижевск, Пушкинская"' in joined
    assert any(note.startswith("curated_public_map_sources=3;") for note in plan.notes)
