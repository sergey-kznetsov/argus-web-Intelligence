import pytest

from argus.contracts.models import CollectionRequest
from argus.research.planner import HeuristicResearchPlanner


@pytest.mark.asyncio
async def test_historical_context_expands_queries():
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
    assert plan.notes == ["heuristic_language=ru"]


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
