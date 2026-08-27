from __future__ import annotations

from types import SimpleNamespace

from argus.orchestrator.adaptive_atomic import AdaptiveResearchAtomicCollectionOrchestrator
from argus.orchestrator.area_atomic import AreaAwareAtomicCollectionOrchestrator
from argus.sources.base import SourceTask


def _record(*, max_pages: int = 18, intents: list[str] | None = None):
    return SimpleNamespace(
        request=SimpleNamespace(
            constraints=SimpleNamespace(max_pages=max_pages),
            intents=intents or ["reviews", "public_mentions"],
        ),
        checkpoint={},
    )


def test_execution_budget_counts_processed_pages_not_candidate_queue():
    orchestrator = object.__new__(AreaAwareAtomicCollectionOrchestrator)
    record = _record(max_pages=18)
    visited = {f"task-{index}" for index in range(5)}

    assert orchestrator._remaining_execution_budget(record, visited) == 12


def test_focused_gap_branch_outranks_generic_depth_crawl():
    requested = {"reviews", "public_mentions"}
    generic = SourceTask(
        source_id="generic_web",
        goal="public_mentions",
        url="https://example.org/category/page-2",
        depth=1,
        metadata={"research_goals": ["public_mentions"]},
    )
    map_gap = SourceTask(
        source_id="generic_web",
        goal="reviews",
        url="https://2gis.ru/perm/firm/123/tab/reviews",
        depth=0,
        metadata={
            "research_goals": ["reviews"],
            "curated_public_map_round": 1,
        },
    )

    assert AdaptiveResearchAtomicCollectionOrchestrator._pending_priority(
        map_gap, requested
    ) < AdaptiveResearchAtomicCollectionOrchestrator._pending_priority(generic, requested)


def test_curated_gap_branch_outranks_general_adaptive_followup():
    requested = {"reviews", "public_mentions"}
    adaptive = SourceTask(
        source_id="generic_web",
        goal="reviews",
        url="https://example.org/reviews",
        metadata={"adaptive_followup_round": 1, "research_goals": ["reviews"]},
    )
    curated = SourceTask(
        source_id="generic_web",
        goal="reviews",
        url="https://2gis.ru/perm/firm/123/tab/reviews",
        metadata={"curated_public_map_round": 1, "research_goals": ["reviews"]},
    )

    assert AdaptiveResearchAtomicCollectionOrchestrator._pending_priority(
        curated, requested
    ) < AdaptiveResearchAtomicCollectionOrchestrator._pending_priority(adaptive, requested)


def test_queue_priority_handles_legacy_task_without_research_goals():
    task = SourceTask(
        source_id="generic_web",
        goal="reviews",
        url="https://example.org/legacy",
    )

    priority = AdaptiveResearchAtomicCollectionOrchestrator._pending_priority(
        task, {"reviews"}
    )

    assert priority[1] == 0


def test_prioritize_pending_moves_focused_branch_to_front_and_records_diagnostics():
    orchestrator = object.__new__(AdaptiveResearchAtomicCollectionOrchestrator)
    generic = SourceTask(
        source_id="generic_web",
        goal="public_mentions",
        url="https://example.org/deep",
        depth=2,
    )
    followup = SourceTask(
        source_id="generic_web",
        goal="reviews",
        url="https://example.org/focused",
        metadata={"adaptive_followup_round": 1, "research_goals": ["reviews"]},
    )
    pending = [generic, followup]
    record = _record()

    orchestrator._prioritize_pending(record, pending)

    assert pending[0] is followup
    assert record.checkpoint["research_queue_priority_version"] == "research-queue-priority/3"
    assert record.checkpoint["research_queue_candidate_count"] == 2
    assert record.checkpoint["research_queue_next"][0]["focused_branch"] == "adaptive_followup"
    assert record.checkpoint["research_queue_next"][0]["fairness_goal"] == "reviews"


def test_equal_priority_tasks_are_interleaved_across_requested_goals():
    orchestrator = object.__new__(AdaptiveResearchAtomicCollectionOrchestrator)
    intents = ["public_mentions", "historical_context", "historical_images"]
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
    record = _record(intents=intents)

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
    # High navigation score still orders candidates inside each goal lane; it just cannot
    # monopolize the entire execution budget anymore.
    mention_urls = [item.url for item in pending if item.goal == "public_mentions"]
    assert mention_urls == [
        "https://mentions.example/0",
        "https://mentions.example/1",
        "https://mentions.example/2",
        "https://mentions.example/3",
    ]


def test_queue_fairness_cursor_rotates_first_goal_after_reprioritization():
    orchestrator = object.__new__(AdaptiveResearchAtomicCollectionOrchestrator)
    record = _record(intents=["first", "second", "third"])
    pending = [
        SourceTask(
            source_id="generic_web",
            goal=goal,
            url=f"https://example.org/{goal}/{index}",
            depth=1,
        )
        for index in range(2)
        for goal in ("first", "second", "third")
    ]

    orchestrator._prioritize_pending(record, pending)
    assert pending[0].goal == "first"
    assert record.checkpoint["research_queue_fairness_cursor"] == 1

    pending.pop(0)
    orchestrator._prioritize_pending(record, pending)
    assert pending[0].goal == "second"
    assert record.checkpoint["research_queue_fairness_cursor"] == 2

    pending.pop(0)
    orchestrator._prioritize_pending(record, pending)
    assert pending[0].goal == "third"
    assert record.checkpoint["research_queue_fairness_cursor"] == 0


def test_fairness_never_demotes_curated_branch_below_generic_other_goal():
    orchestrator = object.__new__(AdaptiveResearchAtomicCollectionOrchestrator)
    record = _record(intents=["public_mentions", "historical_context"])
    generic = SourceTask(
        source_id="generic_web",
        goal="public_mentions",
        url="https://example.org/generic",
        depth=1,
        metadata={"discovery_navigation_score": 1000},
    )
    curated = SourceTask(
        source_id="generic_web",
        goal="historical_context",
        url="https://archive.example/document",
        depth=0,
        metadata={
            "curated_historical_round": 1,
            "discovery_navigation_score": 0,
        },
    )
    pending = [generic, curated]

    orchestrator._prioritize_pending(record, pending)

    assert pending[0] is curated
