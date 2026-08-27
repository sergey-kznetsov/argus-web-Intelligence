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
    assert record.checkpoint["research_queue_priority_version"] == "research-queue-priority/1"
    assert record.checkpoint["research_queue_candidate_count"] == 2
    assert record.checkpoint["research_queue_next"][0]["focused_branch"] == "adaptive_followup"
