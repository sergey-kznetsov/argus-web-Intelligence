from __future__ import annotations

from types import SimpleNamespace

import pytest

from argus.contracts.models import CollectionRequest
from argus.orchestrator.evidence_status import EvidenceStatusAdaptiveResearchOrchestrator
from argus.research.intent_coverage import IntentCoverageEvaluator
from argus.research.supervisor import ResearchSupervisorDecision
from argus.sources.base import SourceTask


def request() -> CollectionRequest:
    return CollectionRequest(
        consumer="production-supervision-test",
        analysis_id="production-supervision-analysis",
        territory={"city": "Пермь", "address": "Комсомольский проспект, 27"},
        intents=["reviews", "public_mentions"],
        constraints={"max_pages": 18, "max_depth": 2},
        allow_partial=True,
    )


class EmptyRepository:
    async def list_observations(self, collection_id: str):
        del collection_id
        return []


class AssessOnlySupervisor:
    target_sources_per_intent = 2

    def __init__(self) -> None:
        self.calls = 0

    async def assess(
        self,
        request,
        observations,
        *,
        errors,
        seen_queries,
        pending_count,
        remaining_page_budget,
    ) -> ResearchSupervisorDecision:
        del request, observations, errors, seen_queries
        assert pending_count == 20
        assert remaining_page_budget == 17
        self.calls += 1
        return ResearchSupervisorDecision(
            continue_research=True,
            priority_intents=["reviews", "public_mentions"],
            query_hints=['"Пермь, Комсомольский проспект, 27" отзывы'],
            flags=["coverage_gap"],
            rationale_ru="Нужно продолжить исследование.",
            model_assisted=True,
            version="assess-only/1",
        )


def build_orchestrator(supervisor=None):
    orchestrator = object.__new__(EvidenceStatusAdaptiveResearchOrchestrator)
    orchestrator.repository = EmptyRepository()
    orchestrator.intent_coverage = IntentCoverageEvaluator()
    orchestrator.research_supervisor = supervisor
    orchestrator.research_supervisor_interval_pages = 4
    orchestrator.research_supervisor_timeout_seconds = 5.0
    orchestrator.max_factual_pending_for_followup = 2
    return orchestrator


def generic_task(index: int, *, goal: str = "reviews", depth: int = 1) -> SourceTask:
    return SourceTask(
        source_id="generic_web",
        goal=goal,
        url=f"https://example.org/fact-{index}",
        depth=depth,
    )


@pytest.mark.asyncio
async def test_supervisor_assesses_factual_gaps_even_with_large_pending_queue():
    supervisor = AssessOnlySupervisor()
    orchestrator = build_orchestrator(supervisor)
    record = SimpleNamespace(
        collection_id="collection-supervision",
        request=request(),
        checkpoint={},
        errors=[],
    )
    pending = [generic_task(index) for index in range(20)]

    result = await orchestrator._refresh_research_supervisor(
        record,
        observations=[],
        pending=pending,
        visited=set(),
    )

    assert supervisor.calls == 1
    assert result["continue_research"] is True
    assert result["priority_intents"] == ["reviews", "public_mentions"]
    assert result["factual_pending_tasks"] == 20
    assert "followup_backpressure" in result["flags"]
    assert result["model_assisted"] is True
    assert result["last_model_decision"]["version"] == "assess-only/1"
    assert record.checkpoint["research_supervisor"] == result


@pytest.mark.asyncio
async def test_deterministic_supervision_exists_without_model_supervisor():
    orchestrator = build_orchestrator(None)
    record = SimpleNamespace(
        collection_id="collection-no-model",
        request=request(),
        checkpoint={},
        errors=[],
    )

    result = await orchestrator._refresh_research_supervisor(
        record,
        observations=[],
        pending=[generic_task(1)],
        visited=set(),
    )

    assert result["continue_research"] is True
    assert result["priority_intents"] == ["reviews", "public_mentions"]
    assert result["model_assisted"] is False
    assert result["model_output_is_evidence"] is False


def test_site_discovery_support_tasks_do_not_count_as_factual_backpressure():
    tasks = [
        generic_task(1),
        SourceTask(
            source_id="site_discovery",
            goal="reviews",
            url="https://example.org/robots.txt",
        ),
        SourceTask(
            source_id="site_discovery",
            goal="reviews",
            url="https://example.org/sitemap.xml",
        ),
    ]

    assert EvidenceStatusAdaptiveResearchOrchestrator._factual_pending_count(tasks) == 1


def test_site_discovery_support_task_is_deferred_behind_deep_factual_task():
    requested = {"reviews"}
    factual = generic_task(1, depth=2)
    support = SourceTask(
        source_id="site_discovery",
        goal="reviews",
        url="https://example.org/robots.txt",
        depth=0,
    )

    assert EvidenceStatusAdaptiveResearchOrchestrator._pending_priority(
        factual,
        requested,
    ) < EvidenceStatusAdaptiveResearchOrchestrator._pending_priority(
        support,
        requested,
    )


def test_queue_priority_uses_current_factual_gaps_not_already_covered_goals():
    orchestrator = object.__new__(EvidenceStatusAdaptiveResearchOrchestrator)
    reviews = generic_task(1, goal="reviews", depth=0)
    mentions = generic_task(2, goal="public_mentions", depth=0)
    pending = [mentions, reviews]
    record = SimpleNamespace(
        request=request(),
        checkpoint={"research_supervisor": {"priority_intents": ["reviews"]}},
    )

    orchestrator._prioritize_pending(record, pending)

    assert pending[0] is reviews
    assert record.checkpoint["research_queue_priority_version"] == "research-queue-priority/3"
