from __future__ import annotations

from copy import deepcopy


_TEST_PROFILES: dict[str, dict[str, object]] = {
    "kraken": {
        "label": "Имитировать запрос Kraken",
        "description": (
            "Публичные городские сообщения, отзывы, жалобы, обсуждения, локальные новости "
            "и происшествия. Профиль только заполняет CollectionRequest и не включает скрытую "
            "логику источников."
        ),
        "consumer": "kraken.simulation",
        "intents": [
            "public_mentions",
            "reviews",
            "comments",
            "complaints",
            "discussions",
            "local_news",
            "incidents",
        ],
        "max_pages": 40,
        "max_depth": 3,
    },
    "janus": {
        "label": "Имитировать запрос Janus",
        "description": (
            "Проверка публичного веб-контура парковочного предложения. Новые parking_* intents "
            "обрабатываются как обычные consumer-neutral исследовательские вопросы."
        ),
        "consumer": "janus.simulation",
        "intents": [
            "parking_supply",
            "parking_capacity",
            "parking_access",
            "parking_pricing",
            "parking_type",
        ],
        "max_pages": 35,
        "max_depth": 2,
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
