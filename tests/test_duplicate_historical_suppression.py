from __future__ import annotations

import pytest

from argus.orchestrator.duplicate_atomic import DuplicateAwareAtomicCollectionOrchestrator
from argus.sources.base import SourceTask


@pytest.mark.asyncio
async def test_duplicate_task_skips_historical_branch_before_touching_orchestrator_state():
    orchestrator = object.__new__(DuplicateAwareAtomicCollectionOrchestrator)
    duplicate_task = SourceTask(
        source_id="generic_web",
        goal="historical_context",
        url="https://example.com/mirror",
        metadata={
            "duplicate_content": True,
            "duplicate_of_observation_id": "obs-original",
        },
    )

    # The duplicate fast-path must return before any repository/discovery/planner
    # state is needed. This makes the suppression deterministic and side-effect free.
    await orchestrator._expand_historical(
        None,
        duplicate_task,
        [],
        [],
        set(),
        set(),
    )
