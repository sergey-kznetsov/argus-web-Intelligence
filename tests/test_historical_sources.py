from argus.contracts.models import CollectionRequest, Observation
from argus.research.historical_sources import (
    RUSSIA_USSR_HISTORICAL_SOURCES,
    HistoricalSourceResearchPlanner,
)


def request() -> CollectionRequest:
    return CollectionRequest(
        consumer="historical-source-test",
        analysis_id="history",
        territory={"city": "Ижевск", "address": "Пушкинская, 277"},
        intents=["historical_context"],
        constraints={"max_pages": 100, "max_depth": 3, "language": "ru"},
    )


def test_catalog_contains_priority_map_photo_archive_and_library_sources():
    by_id = {item.source_id: item for item in RUSSIA_USSR_HISTORICAL_SOURCES}

    assert by_id["pastvu"].visual is True
    assert by_id["etomesto"].kind == "historical_maps"
    assert by_id["retromap"].kind == "historical_maps"
    assert by_id["rgakfd"].kind == "photo_archive"
    assert by_id["presidential_library"].domain == "prlib.ru"
    assert by_id["neb"].domain == "rusneb.ru"
    assert by_id["runivers"].domain == "runivers.ru"
    assert by_id["loc_prokudin_gorskii"].domain == "loc.gov"


def test_initial_context_queries_prioritize_text_archives_before_visual_sources():
    planner = HistoricalSourceResearchPlanner()
    queries = planner.queries(request(), limit=8)

    assert [query.split(' "', 1)[0] for query in queries[:5]] == [
        "site:prlib.ru",
        "site:rusneb.ru",
        "site:archives.gov.ru",
        "site:runivers.ru",
        "site:prozhito.org",
    ]
    joined = "\n".join(queries)
    assert 'site:pastvu.com "Ижевск, Пушкинская, 277"' in joined
    assert 'site:etomesto.ru "Ижевск, Пушкинская, 277"' in joined
    assert 'site:retromap.ru "Ижевск, Пушкинская, 277"' in joined


def test_territory_backed_historical_entity_becomes_new_archive_search_anchor():
    planner = HistoricalSourceResearchPlanner()
    observation = Observation(
        collection_id="collection",
        analysis_id="analysis",
        consumer="test",
        source="generic_web",
        source_kind="web_page",
        url="https://example.org/history",
        entity_type="organization",
        title="Завод Ижмаш старый корпус",
        text=(
            "Ижевск, Пушкинская, 277. Завод Ижмаш старый корпус и история "
            "Ижевского оружейного завода."
        ),
        data={"former_name": "Ижевский оружейный завод"},
        content_hash="a" * 64,
    )
    first_anchor_queries = set(planner.queries(request(), limit=10))
    queries = planner.queries(
        request(),
        observations=[observation],
        seen_queries=first_anchor_queries,
        limit=5,
    )

    assert queries
    assert any("Завод Ижмаш старый корпус" in query for query in queries)


def test_unrelated_observation_cannot_seed_curated_historical_queries():
    planner = HistoricalSourceResearchPlanner()
    unrelated = Observation(
        collection_id="collection",
        analysis_id="analysis",
        consumer="test",
        source="generic_web",
        source_kind="web_page",
        url="https://example.org/moscow",
        entity_type="place",
        title="Октябрьская площадь",
        text="Москва, Октябрьская площадь. История и реконструкция площади.",
        data={"name": "Октябрьская площадь"},
        content_hash="b" * 64,
    )
    initial = set(planner.queries(request(), limit=20))

    queries = planner.queries(
        request(),
        observations=[unrelated],
        seen_queries=initial,
        limit=10,
    )

    assert queries == []


def test_catalog_planner_is_disabled_without_historical_intent():
    ordinary = request().model_copy(update={"intents": ["reviews"]})
    assert HistoricalSourceResearchPlanner().queries(ordinary, limit=10) == []
