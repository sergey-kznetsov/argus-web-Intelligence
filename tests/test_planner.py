import pytest

from argus.contracts.models import CollectionRequest
from argus.research.planner import HeuristicResearchPlanner


@pytest.mark.asyncio
async def test_historical_context_expands_queries_and_reserves_archive_sources():
    request = CollectionRequest(
        consumer="historical",
        analysis_id="1",
        territory={"address": "Москва, Тверская 1"},
        intents=["historical_context"],
    )
    plan = await HeuristicResearchPlanner().plan(request)
    joined = " ".join(plan.queries)
    assert "что было раньше" in joined
    assert "снос" in joined
    assert "site:pastvu.com" in joined
    assert "site:etomesto.ru" in joined
    assert "site:retromap.ru" in joined
    assert "site:photo.rgakfd.ru" in joined
    assert plan.notes[0] == "heuristic_language=ru"
    assert any(note.startswith("curated_historical_sources=") for note in plan.notes)


@pytest.mark.asyncio
async def test_russian_intents_generate_useful_search_terms():
    request = CollectionRequest(
        consumer="test",
        analysis_id="1",
        territory={"city": "Ижевск", "address": "Пушкинская, 277"},
        intents=["reviews", "local_news", "incidents", "discussions"],
    )
    plan = await HeuristicResearchPlanner().plan(request)
    joined = " ".join(plan.queries)

    assert '"Ижевск, Пушкинская, 277" отзывы' in plan.queries
    assert "новости" in joined
    assert "происшествия" in joined
    assert "обсуждение форум" in joined


@pytest.mark.asyncio
async def test_explicit_english_language_uses_english_terms():
    request = CollectionRequest(
        consumer="test",
        analysis_id="1",
        territory={"city": "Helsinki"},
        intents=["reviews", "incidents"],
        constraints={"language": "en"},
    )
    plan = await HeuristicResearchPlanner().plan(request)

    assert '"Helsinki" reviews' in plan.queries
    assert '"Helsinki" incidents' in plan.queries


@pytest.mark.asyncio
async def test_query_budget_covers_each_intent_before_secondary_variants():
    request = CollectionRequest(
        consumer="test",
        analysis_id="fairness",
        territory={"city": "Ижевск"},
        intents=["reviews", "public_mentions", "local_news", "incidents"],
    )
    plan = await HeuristicResearchPlanner(max_queries=4).plan(request)

    assert plan.queries == [
        '"Ижевск" отзывы',
        '"Ижевск"',
        '"Ижевск" новости',
        '"Ижевск" происшествия',
    ]


@pytest.mark.asyncio
async def test_curated_map_queries_use_only_budget_after_primary_intent_coverage():
    request = CollectionRequest(
        consumer="test",
        analysis_id="map-budget",
        territory={"city": "Ижевск"},
        intents=["reviews", "local_news", "incidents", "discussions"],
    )
    plan = await HeuristicResearchPlanner(max_queries=7).plan(request)

    assert plan.queries[:4] == [
        '"Ижевск" отзывы',
        '"Ижевск" новости',
        '"Ижевск" происшествия',
        '"Ижевск" обсуждение форум',
    ]
    assert plan.queries[4].startswith('site:yandex.ru/maps "Ижевск"')
    assert plan.queries[5].startswith('site:2gis.ru "Ижевск"')
    assert plan.queries[6].startswith('site:google.com/maps "Ижевск"')
    assert any(note.startswith("curated_public_map_sources=3;") for note in plan.notes)


@pytest.mark.asyncio
async def test_duplicate_primary_query_does_not_hide_later_unique_round():
    request = CollectionRequest(
        consumer="test",
        analysis_id="duplicate-round",
        territory={"city": "Ижевск"},
        intents=["public_mentions", "public_mentions"],
    )
    plan = await HeuristicResearchPlanner(max_queries=2).plan(request)

    assert plan.queries == ['"Ижевск"', '"Ижевск" упоминания']


@pytest.mark.asyncio
async def test_query_length_is_bounded_without_losing_nonempty_plan():
    request = CollectionRequest(
        consumer="test",
        analysis_id="bounded",
        territory={"address": "А" * 2_000},
        intents=["reviews"],
    )
    plan = await HeuristicResearchPlanner(max_queries=2, max_query_chars=128).plan(request)

    assert plan.queries
    assert all(len(query) <= 512 for query in plan.queries)
    assert len(plan.queries[0]) <= 128


@pytest.mark.asyncio
async def test_unknown_intents_are_round_robin_bounded_too():
    request = CollectionRequest(
        consumer="test",
        analysis_id="unknown",
        territory={"city": "Ижевск"},
        intents=["custom_one", "custom_two", "custom_three"],
    )
    plan = await HeuristicResearchPlanner(max_queries=2).plan(request)

    assert len(plan.queries) == 2
    assert "custom one" in plan.queries[0]
    assert "custom two" in plan.queries[1]
