from __future__ import annotations

from types import SimpleNamespace

from argus.orchestrator.evidence_status import EvidenceStatusAdaptiveResearchOrchestrator
from argus.sources.base import SourceTask


def _record(*intents: str):
    return SimpleNamespace(
        request=SimpleNamespace(intents=list(intents)),
        checkpoint={},
    )


def _source_owned_navigation(
    goal: str = "residential_population",
    *,
    source_id: str = "site_discovery",
    url: str = "https://dom.mingkh.ru/robots.txt",
) -> SourceTask:
    return SourceTask(
        source_id=source_id,
        goal=goal,
        url=url,
        metadata={
            "source_owned_navigation": True,
            "site_discovery_target_source_id": "mingkh_residential",
            "research_goals": ["residential_population", "residential_premises_count"],
        },
    )


def test_source_owned_navigation_is_factual_pending_dependency():
    owned_support = _source_owned_navigation()
    owned_factual = _source_owned_navigation(
        source_id="mingkh_residential",
        url="https://dom.mingkh.ru/permskiy-kray/perm/422906",
    )
    support = SourceTask(
        source_id="site_discovery",
        goal="residential_population",
        url="https://example.org/robots.txt",
    )
    factual = SourceTask(
        source_id="generic_web",
        goal="residential_population",
        url="https://example.org/page",
    )

    assert EvidenceStatusAdaptiveResearchOrchestrator._is_source_owned_navigation(
        owned_support
    ) is True
    assert EvidenceStatusAdaptiveResearchOrchestrator._is_source_owned_navigation(
        owned_factual
    ) is True
    assert EvidenceStatusAdaptiveResearchOrchestrator._factual_pending_count(
        [owned_support, owned_factual, support, factual]
    ) == 3


def test_complete_source_owned_chain_outranks_support_and_adaptive_fallback():
    requested = {"residential_population", "residential_premises_count"}
    owned_support = _source_owned_navigation()
    owned_factual = _source_owned_navigation(
        source_id="mingkh_residential",
        url="https://dom.mingkh.ru/permskiy-kray/perm/422906",
    )
    support = SourceTask(
        source_id="site_discovery",
        goal="residential_population",
        url="https://example.org/robots.txt",
    )
    fallback = SourceTask(
        source_id="mingkh_residential",
        goal="residential_population",
        url="https://dom.mingkh.ru/permskiy-kray/perm/456250",
        metadata={"adaptive_followup_round": 1},
    )

    owned_support_priority = EvidenceStatusAdaptiveResearchOrchestrator._pending_priority(
        owned_support, requested
    )
    owned_factual_priority = EvidenceStatusAdaptiveResearchOrchestrator._pending_priority(
        owned_factual, requested
    )
    support_priority = EvidenceStatusAdaptiveResearchOrchestrator._pending_priority(
        support, requested
    )
    fallback_priority = EvidenceStatusAdaptiveResearchOrchestrator._pending_priority(
        fallback, requested
    )

    assert owned_support_priority[0] == -1
    assert owned_factual_priority[0] == -1
    assert owned_support_priority < support_priority
    assert owned_factual_priority < fallback_priority


def test_production_orchestrator_uses_round_robin_fairness_from_adaptive_queue():
    orchestrator = object.__new__(EvidenceStatusAdaptiveResearchOrchestrator)
    record = _record("public_mentions", "historical_context", "historical_images")
    pending: list[SourceTask] = []
    for index in range(4):
        pending.append(
            SourceTask(
                source_id="generic_web",
                goal="public_mentions",
                url=f"https://mentions.example/{index}",
                depth=1,
                metadata={"discovery_navigation_score": 100 - index},
            )
        )
    for index in range(4):
        pending.append(
            SourceTask(
                source_id="generic_web",
                goal="historical_context",
                url=f"https://history.example/{index}",
                depth=1,
                metadata={"discovery_navigation_score": 10 - index},
            )
        )
    for index in range(4):
        pending.append(
            SourceTask(
                source_id="generic_web",
                goal="historical_images",
                url=f"https://images.example/{index}",
                depth=1,
                metadata={"discovery_navigation_score": 1 - index},
            )
        )

    orchestrator._prioritize_pending(record, pending)

    assert [item.goal for item in pending[:9]] == [
        "public_mentions",
        "historical_context",
        "historical_images",
        "public_mentions",
        "historical_context",
        "historical_images",
        "public_mentions",
        "historical_context",
        "historical_images",
    ]
    assert record.checkpoint["research_queue_priority_version"] == "research-queue-priority/6"
    assert record.checkpoint["research_queue_next"][0]["fairness_goal"] == "public_mentions"
    assert record.checkpoint["research_queue_priority_intents"] == []


def test_live_factual_gap_outranks_already_covered_goals():
    orchestrator = object.__new__(EvidenceStatusAdaptiveResearchOrchestrator)
    record = _record("public_mentions", "historical_context", "historical_images")
    record.checkpoint = {
        "research_supervisor": {
            "priority_intents": ["historical_context"],
            "factual_coverage_counts": {
                "public_mentions": 10,
                "historical_context": 0,
                "historical_images": 20,
            },
            "target_sources_per_intent": 2,
        }
    }
    pending = [
        SourceTask(
            source_id="generic_web",
            goal="public_mentions",
            url=f"https://mentions.example/{index}",
            depth=1,
        )
        for index in range(8)
    ]
    pending.extend(
        [
            SourceTask(
                source_id="generic_web",
                goal="historical_images",
                url="https://images.example/covered",
                depth=1,
            ),
            SourceTask(
                source_id="generic_web",
                goal="historical_context",
                url="https://history.example/gap",
                depth=1,
            ),
        ]
    )

    orchestrator._prioritize_pending(record, pending)

    assert pending[0].url == "https://history.example/gap"
    assert record.checkpoint["research_queue_next_goal"] == "historical_context"
    assert record.checkpoint["research_queue_priority_intents"] == ["historical_context"]
    assert record.checkpoint["research_queue_next"][0]["factual_gap"] is True
    assert record.checkpoint["research_queue_factual_coverage_counts"] == {
        "public_mentions": 10,
        "historical_context": 0,
        "historical_images": 20,
    }


def test_explicit_task_goal_prevents_broad_metadata_from_stealing_gap_priority():
    orchestrator = object.__new__(EvidenceStatusAdaptiveResearchOrchestrator)
    record = _record("public_mentions", "historical_context")
    record.checkpoint = {
        "research_supervisor": {
            "priority_intents": ["historical_context"],
            "factual_coverage_counts": {
                "public_mentions": 4,
                "historical_context": 0,
            },
            "target_sources_per_intent": 2,
        }
    }
    context = SourceTask(
        source_id="generic_web",
        goal="historical_context",
        url="https://example.org/context",
        depth=2,
    )
    broad_mentions = SourceTask(
        source_id="generic_web",
        goal="public_mentions",
        url="https://example.org/broad-mentions",
        depth=0,
        metadata={"research_goals": ["public_mentions", "historical_context"]},
    )
    pending = [broad_mentions, context]

    orchestrator._prioritize_pending(record, pending)

    assert pending[0] is context
    assert record.checkpoint["research_queue_next_goal"] == "historical_context"
    assert record.checkpoint["research_queue_next"][0]["factual_gap_intents"] == [
        "historical_context"
    ]
    broad_summary = next(
        item
        for item in record.checkpoint["research_queue_next"]
        if item["goal"] == "public_mentions"
    )
    assert broad_summary["factual_gap"] is False
    assert broad_summary["factual_gap_intents"] == []


def test_task_without_explicit_goal_falls_back_to_research_goals_for_queue_routing():
    task = SourceTask(
        source_id="site_discovery",
        goal="",
        url="https://example.org/support",
        metadata={"research_goals": ["historical_context", "public_mentions"]},
    )

    assert EvidenceStatusAdaptiveResearchOrchestrator._task_intents(task) == {
        "historical_context",
        "public_mentions",
    }


def test_all_covered_or_unsupervised_queue_keeps_shared_fair_order():
    orchestrator = object.__new__(EvidenceStatusAdaptiveResearchOrchestrator)
    record = _record("first", "second")
    record.checkpoint = {
        "research_supervisor": {
            "priority_intents": [],
            "factual_coverage_counts": {"first": 3, "second": 2},
            "target_sources_per_intent": 2,
        }
    }
    pending = [
        SourceTask(
            source_id="generic_web",
            goal=goal,
            url=f"https://example.org/{goal}/{index}",
            depth=1,
        )
        for index in range(3)
        for goal in ("first", "second")
    ]

    orchestrator._prioritize_pending(record, pending)

    assert [task.goal for task in pending[:4]] == ["first", "second", "first", "second"]
    assert all(not item["factual_gap"] for item in record.checkpoint["research_queue_next"])
