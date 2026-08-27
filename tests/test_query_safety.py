import pytest

from argus.contracts.models import CollectionRequest
from argus.research.followup import FollowupPlan
from argus.research.planner import ResearchPlan
from argus.research.query_safety import (
    QuerySafeFollowupResearchPlanner,
    QuerySafeResearchPlanner,
    sanitize_research_queries,
)


def perm_request(*intents: str) -> CollectionRequest:
    return CollectionRequest(
        consumer="kraken-simulation",
        analysis_id="perm-query-safety",
        territory={"city": "Пермь", "address": "Комсомольский проспект, 27"},
        intents=list(intents) or ["public_mentions"],
    )


def test_nested_llm_query_objects_do_not_leak_schema_words_into_search():
    values = [
        "{'queries': ['public_mentions', 'around: Comsomol Street 27, Perm', 'metadata: public']}",
        {"search_string": "local media incidents", "notes": ["ignore me"]},
        {"queries": [{"query": "жалобы жителей"}]},
    ]

    queries = sanitize_research_queries(
        values,
        perm_request("public_mentions", "incidents", "complaints"),
        max_queries=8,
    )

    assert all("{'" not in query and '"queries"' not in query for query in queries)
    assert all("metadata: public" not in query for query in queries)
    assert all("ignore me" not in query for query in queries)
    assert all("Комсомольский проспект, 27" in query for query in queries)
    assert not any(query.endswith("public_mentions") for query in queries)
    assert any("local media incidents" in query for query in queries)
    assert any("жалобы жителей" in query for query in queries)


def test_existing_territorial_query_is_not_prefixed_twice():
    query = '"Пермь, Комсомольский проспект, 27" отзывы'

    assert sanitize_research_queries(
        [query],
        perm_request("reviews"),
        max_queries=2,
    ) == [query]


def test_house_number_alone_cannot_approve_geographically_drifted_llm_query():
    query = "public records around Peremysk district, 27"

    assert sanitize_research_queries(
        [query],
        perm_request("public_mentions"),
        max_queries=2,
    ) == ['"Пермь, Комсомольский проспект, 27" public records around Peremysk district, 27']


def test_city_and_house_without_specific_street_token_are_reanchored():
    query = "Пермь public comments near avenue 27"

    assert sanitize_research_queries(
        [query],
        perm_request("comments"),
        max_queries=2,
    ) == ['"Пермь, Комсомольский проспект, 27" Пермь public comments near avenue 27']


def test_bare_custom_intent_is_rejected_and_seen_query_is_deduplicated():
    queries = sanitize_research_queries(
        ["parking_capacity", "парковка вместимость"],
        perm_request("parking_capacity"),
        max_queries=4,
        seen_queries={'"Пермь, Комсомольский проспект, 27" парковка вместимость'},
    )

    assert queries == []


class BadPlanner:
    async def plan(self, request):
        del request
        return ResearchPlan(queries=[{"notes": ["no query"]}])


class GoodFallbackPlanner:
    async def plan(self, request):
        del request
        return ResearchPlan(queries=['"Пермь, Комсомольский проспект, 27" отзывы'])


@pytest.mark.asyncio
async def test_research_planner_uses_deterministic_fallback_when_llm_output_has_no_queries():
    planner = QuerySafeResearchPlanner(
        BadPlanner(),
        fallback=GoodFallbackPlanner(),
        max_queries=4,
    )

    plan = await planner.plan(perm_request("reviews"))

    assert plan.queries == ['"Пермь, Комсомольский проспект, 27" отзывы']
    assert plan.notes[-1] == "query_safety=query-safety/2"


class BadFollowupPlanner:
    async def plan_followups(self, request, observations, *, seen_queries, max_queries):
        del request, observations, seen_queries, max_queries
        return FollowupPlan(queries=["{'search_string': 'local_news', 'notes': ['wrong shape']}"])


class GoodFollowupFallback:
    async def plan_followups(self, request, observations, *, seen_queries, max_queries):
        del request, observations, seen_queries, max_queries
        return FollowupPlan(queries=['"Пермь, Комсомольский проспект, 27" новости СМИ'])


@pytest.mark.asyncio
async def test_followup_planner_uses_fallback_when_only_bare_intent_survives_shape_parsing():
    planner = QuerySafeFollowupResearchPlanner(
        BadFollowupPlanner(),
        fallback=GoodFollowupFallback(),
    )

    plan = await planner.plan_followups(
        perm_request("local_news"),
        [],
        seen_queries=set(),
        max_queries=2,
    )

    assert plan.queries == ['"Пермь, Комсомольский проспект, 27" новости СМИ']
    assert plan.notes[-1] == "query_safety=query-safety/2"
