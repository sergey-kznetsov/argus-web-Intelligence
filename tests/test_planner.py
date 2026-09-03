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
    # Generic search reserves bounded space for curated archive/catalog sources. Dedicated
    # historical photo sources such as PastVu are handled by their own adapters instead of
    # being duplicated as generic search queries.
    assert "site:prlib.ru" in joined
    assert "site:rusneb.ru" in joined
    assert "site:archives.gov.ru" in joined
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

    assert "Ижевск, Пушкинская, 277 отзывы" in plan.queries
    assert "новости" in joined
    assert "происшествия" in joined
    assert "обсуждение форум" in joined


@pytest.mark.asyncio
async def test_kraken_canonical_intents_use_real_russian_search_phrases():
    request = CollectionRequest(
        consumer="kraken.development.uds",
        consumer_profile_version=1,
        capability="urban_signals",
        analysis_id="kraken-intents",
        territory={"city": "Ижевск", "address": "Пушкинская, 277"},
        intents=["posts", "public_appeals", "resident_messages", "local_news"],
    )
    plan = await HeuristicResearchPlanner(max_queries=8).plan(request)
    joined = " ".join(plan.queries)

    assert "посты" in joined
    assert "обращения граждан" in joined
    assert "сообщения жителей" in joined
    assert "новости" in joined
    assert "public appeals" not in joined
    assert "resident messages" not in joined


@pytest.mark.asyncio
async def test_public_mentions_address_is_not_forced_into_exact_phrase():
    request = CollectionRequest(
        consumer="test",
        analysis_id="address-discovery",
        territory={"city": "Ижевск", "address": "Ижевск, Пушкинская, 277"},
        intents=["public_mentions"],
    )
    plan = await HeuristicResearchPlanner().plan(request)

    assert plan.queries == [
        "Ижевск, Пушкинская, 277",
        "Ижевск, Пушкинская, 277 упоминания",
    ]
    assert all(not query.startswith('"') for query in plan.queries)


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

    assert "Helsinki reviews" in plan.queries
    assert "Helsinki incidents" in plan.queries


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
        "Ижевск отзывы",
        "Ижевск",
        "Ижевск новости",
        "Ижевск происшествия",
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
        "Ижевск отзывы",
        "Ижевск новости",
        "Ижевск происшествия",
        "Ижевск обсуждение форум",
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

    assert plan.queries == ["Ижевск", "Ижевск упоминания"]


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
    assert all(len(query) <= 128 for query in plan.queries)


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
