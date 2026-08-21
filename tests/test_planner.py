import pytest

from argus.contracts.models import CollectionRequest
from argus.research.planner import HeuristicResearchPlanner


@pytest.mark.asyncio
async def test_historical_context_expands_queries():
    request = CollectionRequest(consumer="historical", analysis_id="1",
                                territory={"address": "Москва, Тверская 1"},
                                intents=["historical_context"])
    plan = await HeuristicResearchPlanner().plan(request)
    joined = " ".join(plan.queries)
    assert "что было раньше" in joined
    assert "снос" in joined
