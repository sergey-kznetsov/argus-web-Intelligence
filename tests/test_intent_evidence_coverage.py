from __future__ import annotations

import pytest

from argus.config import Settings
from argus.contracts.models import CollectionRequest, Observation
from argus.research.coverage import (
    EvidenceAwareHeuristicFollowupResearchPlanner,
    IntentCoverageEvaluator,
)
from argus.research.followup import (
    HeuristicFollowupResearchPlanner,
    OllamaFollowupResearchPlanner,
)


def observation(
    *,
    entity_type: str = "document",
    source_kind: str = "web_page",
    url: str = "https://example.org/page",
    goals: list[str] | None = None,
    provenance: dict[str, object] | None = None,
) -> Observation:
    data: dict[str, object] = {}
    if goals is not None:
        data["research_goals"] = goals
    return Observation(
        observation_id=f"obs-{entity_type}-{source_kind}",
        collection_id="collection-1",
        analysis_id="analysis-1",
        consumer="test",
        source="generic_web",
        source_kind=source_kind,
        url=url,
        entity_type=entity_type,
        title="Source page",
        text="Public factual content",
        data=data,
        content_hash="a" * 64,
        provenance=provenance or {},
        quality={},
    )


def request(*intents: str) -> CollectionRequest:
    return CollectionRequest(
        consumer="coverage-test",
        analysis_id="coverage-analysis",
        territory={"city": "Ижевск", "address": "Пушкинская, 277"},
        intents=list(intents),
        constraints={"max_pages": 20},
    )


def test_research_goal_metadata_does_not_count_as_review_evidence():
    evaluator = IntentCoverageEvaluator()
    page = observation(goals=["reviews"])

    assert evaluator.supports(page, "reviews") is False
    assert evaluator.counts([page]).get("reviews", 0) == 0


def test_source_declared_review_entity_counts_as_review_evidence():
    evaluator = IntentCoverageEvaluator()
    review = observation(entity_type="review", source_kind="json_ld", goals=["reviews"])

    assert evaluator.supports(review, "reviews") is True
    assert evaluator.counts([review])["reviews"] == 1


def test_tracking_url_variants_count_as_one_factual_source():
    evaluator = IntentCoverageEvaluator()
    first = observation(
        entity_type="review",
        source_kind="json_ld",
        url="https://example.org/place?utm_source=search&gclid=one#reviews",
    )
    second = observation(
        entity_type="review",
        source_kind="json_ld",
        url="https://EXAMPLE.org:443/place?utm_medium=cpc&yclid=two",
    )

    assert evaluator.counts([first, second])["reviews"] == 1


def test_feed_entry_counts_as_local_news_without_research_goal_credit():
    evaluator = IntentCoverageEvaluator()
    feed = observation(
        entity_type="publication",
        source_kind="feed_entry",
        url="https://news.example.org/item/1",
        goals=["public_mentions"],
    )

    counts = evaluator.counts([feed])
    assert counts["local_news"] == 1
    assert counts["public_mentions"] == 1


def test_public_mention_requires_territory_relevance_when_request_is_known():
    evaluator = IntentCoverageEvaluator()
    unrelated = observation(goals=["public_mentions"])
    relevant = observation(goals=["public_mentions"]).model_copy(
        update={"text": "Кофейня находится по адресу ул. Пушкинская, д. 277."}
    )
    current_request = request("public_mentions")

    assert evaluator.supports(unrelated, "public_mentions") is True
    assert (
        evaluator.supports(unrelated, "public_mentions", request=current_request)
        is False
    )
    assert evaluator.supports(relevant, "public_mentions", request=current_request) is True
    assert evaluator.counts([unrelated], request=current_request).get("public_mentions", 0) == 0
    assert evaluator.counts([relevant], request=current_request)["public_mentions"] == 1


def test_city_only_public_mention_accepts_conservative_city_case_form():
    evaluator = IntentCoverageEvaluator()
    current_request = CollectionRequest(
        consumer="coverage-test",
        analysis_id="city-only",
        territory={"city": "Ижевск"},
        intents=["public_mentions"],
    )
    inflected = observation().model_copy(
        update={"text": "Новый объект открылся в Ижевске."}
    )

    # ARGUS accepts a bounded exact-token Russian locative alias, not fuzzy matching.
    assert evaluator.supports(inflected, "public_mentions", request=current_request) is True
    exact = inflected.model_copy(update={"text": "Ижевск: новый объект открылся сегодня."})
    assert evaluator.supports(exact, "public_mentions", request=current_request) is True


def test_archive_capture_counts_as_historical_context():
    evaluator = IntentCoverageEvaluator()
    historical = observation(
        provenance={
            "archive": {
                "historical_capture": True,
                "original_url": "https://example.org/old",
            }
        }
    )

    assert evaluator.supports(historical, "historical_context") is True


def test_default_ollama_followup_does_not_credit_navigation_goal_as_evidence():
    planner = OllamaFollowupResearchPlanner(Settings())
    page = observation(goals=["reviews"])

    summary = planner._summary(request("reviews"), [page])

    assert planner._factual_coverage_counts([page]).get("reviews", 0) == 0
    assert summary == [
        {
            "source": "generic_web",
            "source_kind": "web_page",
            "entity_type": "document",
            "title": "Source page",
            "host": "example.org",
            "published_at": None,
            "navigation_goals": ["reviews"],
            "navigation_goals_are_evidence": False,
            "evidenced_intents": [],
        }
    ]


def test_default_ollama_followup_marks_actual_review_as_evidenced():
    planner = OllamaFollowupResearchPlanner(Settings())
    review = observation(
        entity_type="review",
        source_kind="json_ld",
        goals=["public_mentions"],
    ).model_copy(
        update={"text": "Ижевск, Пушкинская, 277. Публичный отзыв о месте."}
    )

    summary = planner._summary(request("reviews"), [review])

    assert planner._factual_coverage_counts([review])["reviews"] == 1
    assert summary[0]["navigation_goals"] == ["public_mentions"]
    assert summary[0]["evidenced_intents"] == ["reviews"]


@pytest.mark.asyncio
async def test_default_heuristic_keeps_searching_when_review_goal_has_no_review_fact():
    planner = HeuristicFollowupResearchPlanner(target_hits_per_intent=1)
    page = observation(goals=["reviews"])

    plan = await planner.plan_followups(
        request("reviews"),
        [page],
        seen_queries=set(),
        max_queries=4,
    )

    assert len(plan.queries) == 1
    assert "отзывы" in plan.queries[0]
    assert plan.notes == ["coverage_gap:reviews:0"]


@pytest.mark.asyncio
async def test_default_heuristic_keeps_searching_after_irrelevant_public_mention_page():
    planner = HeuristicFollowupResearchPlanner(target_hits_per_intent=1)
    page = observation(goals=["public_mentions"])

    plan = await planner.plan_followups(
        request("public_mentions"),
        [page],
        seen_queries=set(),
        max_queries=4,
    )

    assert len(plan.queries) == 1
    assert "упоминания" in plan.queries[0]
    assert plan.notes == ["coverage_gap:public_mentions:0"]


@pytest.mark.asyncio
async def test_default_heuristic_stops_after_territory_backed_public_mention():
    planner = HeuristicFollowupResearchPlanner(target_hits_per_intent=1)
    page = observation(goals=["public_mentions"]).model_copy(
        update={"text": "Адрес объекта: Пушкинская, 277, Ижевск."}
    )

    plan = await planner.plan_followups(
        request("public_mentions"),
        [page],
        seen_queries=set(),
        max_queries=4,
    )

    assert plan.queries == []


@pytest.mark.asyncio
async def test_followup_planner_keeps_searching_when_review_goal_has_no_review_fact():
    planner = EvidenceAwareHeuristicFollowupResearchPlanner(target_hits_per_intent=1)
    page = observation(goals=["reviews"])

    plan = await planner.plan_followups(
        request("reviews"),
        [page],
        seen_queries=set(),
        max_queries=4,
    )

    assert len(plan.queries) == 1
    assert "отзывы" in plan.queries[0]
    assert plan.notes == ["coverage_gap:reviews:0"]


@pytest.mark.asyncio
async def test_followup_planner_stops_review_query_after_actual_review_fact():
    planner = EvidenceAwareHeuristicFollowupResearchPlanner(target_hits_per_intent=1)
    review = observation(
        entity_type="review",
        source_kind="json_ld",
        goals=["reviews"],
    ).model_copy(
        update={"text": "Ижевск, Пушкинская, 277. Публичный отзыв о месте."}
    )

    plan = await planner.plan_followups(
        request("reviews"),
        [review],
        seen_queries=set(),
        max_queries=4,
    )

    assert plan.queries == []
