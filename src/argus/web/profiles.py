from __future__ import annotations

from copy import deepcopy


_TEST_PROFILES: dict[str, dict[str, object]] = {
    "kraken": {
        "label": "Имитировать запрос Kraken",
        "description": (
            "Публичные сообщения граждан о городских проблемах: жалобы, обращения, "
            "обсуждения, сообщения жителей, локальные новости и происшествия. Профиль "
            "только заполняет CollectionRequest и не включает скрытую логику источников."
        ),
        "consumer": "kraken.development.uds",
        "intents": [
            "comments",
            "discussions",
            "complaints",
            "incidents",
            "posts",
            "public_appeals",
            "resident_messages",
            "local_news",
        ],
        "max_pages": 40,
        "max_depth": 3,
    },
    "janus": {
        "label": "Имитировать запрос Janus",
        "description": (
            "Изолированный контур Janus: получить с dom.mingkh.ru только фактическое "
            "количество жилых помещений для конкретного дома. ARGUS не анализирует "
            "парковки и не рассчитывает парковочный потенциал."
        ),
        "consumer": "janus.parking.potential.uds",
        "consumer_profile_version": 1,
        "capability": "residential_facts",
        "requested_facts": ["residential_premises_count"],
        "intents": ["residential_premises_count"],
        "max_pages": 1,
        "max_depth": 0,
    },
    "historical": {
        "label": "Имитировать исторический модуль",
        "description": (
            "История места, старые названия и организации, архивные публикации, карты, "
            "фотографии и связанные исторические сущности."
        ),
        "consumer": "historical.simulation",
        "intents": [
            "historical_context",
            "historical_images",
            "public_mentions",
        ],
        "max_pages": 50,
        "max_depth": 4,
    },
}


def web_test_profiles() -> dict[str, dict[str, object]]:
    """Return isolated operator fixtures used only to fill CollectionRequest fields.

    Profiles must never be consumed by the ARGUS core, planner or source registry. The
    backend continues to route solely from territory, intents, constraints and evidence.
    """

    return deepcopy(_TEST_PROFILES)
