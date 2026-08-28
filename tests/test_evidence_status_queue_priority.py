from __future__ import annotations

from types import SimpleNamespace

from argus.orchestrator.evidence_status import EvidenceStatusAdaptiveResearchOrchestrator
from argus.sources.base import SourceTask


def _record(*intents: str):
    return SimpleNamespace(
        request=SimpleNamespace(intents=list(intents)),
        checkpoint={},
    )


def _source_owned_navigation(goal: str = "residential_population") -> SourceTask:
    return SourceTask(
        source_id="site_discovery",
        goal=goal,
        url="https://dom.mingkh.ru/robots.txt",
        metadata={
            "source_owned_navigation": True,
            "site_discovery_target_source_id": "mingkh_residential",
            "research_goals": ["residential_population", "residential_premises_count"],
        },
    )


def test_source_owned_navigation_is_factual_pending_dependency():
    owned = _source_owned_navigation()
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

    assert EvidenceStatusAdaptiveResearchOrchestrator._factual_pending_count(
        [owned, support, factual]
    ) == 2


def test_source_owned_navigation_is_not_demoted_like_support_only_sitemap_work():
    requested = {"residential_population", "residential_premises_count"}
    owned = _source_owned_navigation()
    support = SourceTask(
        source_id="site_discovery",
        goal="residential_population",
        url="https://example.org/robots.txt",
    )

    assert EvidenceStatusAdaptiveResearchOrchestrator._pending_priority(
        owned, requested
    ) < EvidenceStatusAdaptiveResearchOrchestrator._pending_priority(support, requested)


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
    assert record.checkpoint["research_queue_priority_version"] == "research-queue-priority/4"
    assert record.checkpoint["research_queue_next"][0]["fairness_goal"] == "public_mentions"
