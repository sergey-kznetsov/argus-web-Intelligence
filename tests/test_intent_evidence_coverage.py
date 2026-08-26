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
    review = observation(entity_type="review", source_kind="json_ld", goals=["reviews"])

    plan = await planner.plan_followups(
        request("reviews"),
        [review],
        seen_queries=set(),
        max_queries=4,
    )

    assert plan.queries == []
