from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from argus.orchestrator.adaptive_atomic import AdaptiveResearchAtomicCollectionOrchestrator
from argus.orchestrator.evidence_status import EvidenceStatusAdaptiveResearchOrchestrator
from argus.sources.base import SourceTask


@pytest.mark.asyncio
async def test_optional_research_expansion_timeout_is_contained(monkeypatch):
    async def slow_expand(self, record, task, observations, pending, visited, seen_queries):
        del self, record, task, observations, pending, visited, seen_queries
        await asyncio.sleep(0.05)

    monkeypatch.setattr(
        AdaptiveResearchAtomicCollectionOrchestrator,
        "_expand_historical",
        slow_expand,
    )
    orchestrator = object.__new__(EvidenceStatusAdaptiveResearchOrchestrator)
    orchestrator.research_expansion_timeout_seconds = 0.01
    record = SimpleNamespace(checkpoint={}, errors=[])
    task = SourceTask(
        source_id="generic_web",
        goal="public_mentions",
        url="https://example.com/source",
    )

    await orchestrator._expand_historical(record, task, [], [], set(), set())

    assert record.checkpoint["research_expansion_timeout_count"] == 1
    assert record.checkpoint["research_expansion_timeout_seconds"] == 0.01
    assert len(record.errors) == 1
    assert record.errors[0].code == "RESEARCH_EXPANSION_TIMEOUT"
    assert record.errors[0].source_id == "generic_web"
    assert record.errors[0].retryable is True


@pytest.mark.asyncio
async def test_repeated_expansion_timeouts_increment_checkpoint_without_error_spam(monkeypatch):
    async def slow_expand(self, record, task, observations, pending, visited, seen_queries):
        del self, record, task, observations, pending, visited, seen_queries
        await asyncio.sleep(0.05)

    monkeypatch.setattr(
        AdaptiveResearchAtomicCollectionOrchestrator,
        "_expand_historical",
        slow_expand,
    )
    orchestrator = object.__new__(EvidenceStatusAdaptiveResearchOrchestrator)
    orchestrator.research_expansion_timeout_seconds = 0.01
    record = SimpleNamespace(checkpoint={}, errors=[])
    task = SourceTask(
        source_id="generic_web",
        goal="historical_context",
        url="https://example.com/history",
    )

    await orchestrator._expand_historical(record, task, [], [], set(), set())
    await orchestrator._expand_historical(record, task, [], [], set(), set())

    assert record.checkpoint["research_expansion_timeout_count"] == 2
    assert [error.code for error in record.errors] == ["RESEARCH_EXPANSION_TIMEOUT"]
