from __future__ import annotations

from types import SimpleNamespace

from argus.contracts.models import SourceCoverage, StructuredError
from argus.orchestrator.atomic import AtomicCollectionOrchestrator
from argus.sources.base import SourceTask


def _task(url: str, *, source_id: str = "mingkh_residential") -> SourceTask:
    return SourceTask(
        source_id=source_id,
        goal="residential_population",
        url=url,
    )


def _coverage() -> SourceCoverage:
    return SourceCoverage(
        source_id="mingkh_residential",
        status="blocked",
        blocked=True,
        error_code="MINGKH_ACCESS_CHALLENGE",
        error_message="blocked",
    )


def test_repeated_same_host_block_opens_cutoff_and_preserves_other_hosts():
    record = SimpleNamespace(checkpoint={})
    pending = [
        _task("https://dom.mingkh.ru/perm/1"),
        _task("https://dom.mingkh.ru/perm/2"),
        _task("https://other.example/page", source_id="mingkh_residential"),
        _task("https://dom.mingkh.ru/perm/3", source_id="generic_web"),
    ]
    current = _task("https://dom.mingkh.ru/perm")

    assert AtomicCollectionOrchestrator._apply_source_block_circuit_breaker(
        record, pending, current, _coverage()
    ) is False
    assert AtomicCollectionOrchestrator._apply_source_block_circuit_breaker(
        record, pending, current, _coverage()
    ) is False
    assert AtomicCollectionOrchestrator._apply_source_block_circuit_breaker(
        record, pending, current, _coverage()
    ) is True

    assert [item.url for item in pending] == [
        "https://other.example/page",
        "https://dom.mingkh.ru/perm/3",
    ]
    state = record.checkpoint["source_block_circuit_breakers"]
    entry = state["mingkh_residential|dom.mingkh.ru|MINGKH_ACCESS_CHALLENGE"]
    assert entry["count"] == 3
    assert entry["open"] is True
    assert entry["pending_tasks_removed"] == 2


def test_blocked_error_and_no_query_warning_do_not_turn_blocked_result_into_failure():
    record = SimpleNamespace(
        coverage=[
            SourceCoverage(
                source_id="mingkh_residential",
                status="blocked",
                blocked=True,
                error_code="MINGKH_ACCESS_CHALLENGE",
            )
        ],
        errors=[
            StructuredError(
                code="MINGKH_ACCESS_CHALLENGE",
                message="blocked",
                source_id="mingkh_residential",
            ),
            StructuredError(
                code="DISCOVERY_NO_QUERIES",
                message="direct source task was used instead",
                source_id="discovery",
            ),
        ],
    )

    assert AtomicCollectionOrchestrator._terminal_source_errors(record) == []
