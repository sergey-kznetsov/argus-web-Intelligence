from __future__ import annotations

from argus.contracts.models import CollectionRequest, Observation
from argus.research.historical import HistoricalBranchPlanner


def _request() -> CollectionRequest:
    return CollectionRequest(
        consumer="historical-anchor-test",
        analysis_id="history-anchor",
        territory={"city": "Пермь", "address": "Комсомольский проспект, 27"},
        intents=["historical_context"],
        constraints={"max_pages": 20, "language": "ru"},
    )


def _observation(*, title: str, text: str, suffix: str) -> Observation:
    return Observation(
        observation_id=f"observation-{suffix}",
        collection_id="collection-history-anchor",
        analysis_id="history-anchor",
        consumer="historical-anchor-test",
        source="generic_web",
        source_kind="web_page",
        url=f"https://example.org/{suffix}",
        entity_type="document",
        title=title,
        text=text,
        content_hash=(suffix[0] if suffix else "a") * 64,
    )


def test_historical_branch_uses_entity_from_territory_backed_observation():
    planner = HistoricalBranchPlanner()
    relevant = _observation(
        title="Дом купца Иванова",
        text="Пермь, Комсомольский проспект, 27. Дом купца Иванова, историческая справка.",
        suffix="relevant",
    )

    queries = planner.expand(_request(), [relevant], seen_queries=set())

    assert queries
    assert any("Дом купца Иванова" in query for query in queries)


def test_historical_branch_rejects_entity_from_another_city_even_if_title_looks_historical():
    planner = HistoricalBranchPlanner()
    unrelated = _observation(
        title="Октябрьская площадь",
        text="Екатеринбург. Октябрьская площадь, история застройки центра города.",
        suffix="unrelated",
    )

    queries = planner.expand(_request(), [unrelated], seen_queries=set())

    assert queries == []


def test_historical_branch_rejects_unproven_search_like_title_without_source_territory():
    planner = HistoricalBranchPlanner()
    unproven = _observation(
        title="октябрьская площадь на карте москвы - Площадь PRO",
        text="Каталог городских площадей и карт.",
        suffix="unproven",
    )

    queries = planner.expand(_request(), [unproven], seen_queries=set())

    assert queries == []
