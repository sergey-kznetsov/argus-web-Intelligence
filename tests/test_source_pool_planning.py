from __future__ import annotations

import pytest

from argus.contracts.models import CollectionRequest
from argus.research.planner import HeuristicResearchPlanner


def request() -> CollectionRequest:
    return CollectionRequest(
        consumer="kraken.simulation",
        analysis_id="source-pool-test",
        territory={"city": "Пермь", "address": "Комсомольский проспект, 27"},
        intents=["reviews", "public_mentions"],
        constraints={
            "max_pages": 20,
            "max_depth": 2,
            "source_pool_urls": [
                "https://example.org/local-source",
                "https://example.net/another-source",
            ],
        },
    )


def test_collection_defaults_to_russian_output_language():
    value = request()

    assert value.constraints.output_language == "ru"
    assert [str(item) for item in value.constraints.source_pool_urls] == [
        "https://example.org/local-source",
        "https://example.net/another-source",
    ]


@pytest.mark.asyncio
async def test_source_pool_urls_become_normal_planner_tasks_not_seed_priority():
    value = request()
    plan = await HeuristicResearchPlanner(max_queries=4).plan(value)

    assert plan.queries
    assert len(plan.tasks) == 2
    assert [task.url for task in plan.tasks] == [
        "https://example.org/local-source",
        "https://example.net/another-source",
    ]
    assert all(task.source_id == "generic_web" for task in plan.tasks)
    assert all(task.metadata["research_goals"] == ["reviews", "public_mentions"] for task in plan.tasks)
    assert all(task.metadata["source_pool"]["kind"] == "supplemental" for task in plan.tasks)
    assert all(task.metadata["source_pool"]["priority"] == "normal" for task in plan.tasks)
    assert all(task.metadata["source_pool"]["is_evidence"] is False for task in plan.tasks)
    assert any("supplemental_source_pool=2" in note for note in plan.notes)


def test_seed_urls_and_source_pool_urls_remain_separate_contracts():
    value = CollectionRequest(
        consumer="probe",
        analysis_id="source-pool-separation",
        territory={"city": "Пермь"},
        intents=["public_mentions"],
        constraints={
            "seed_urls": ["https://example.org/must-check"],
            "source_pool_urls": ["https://example.org/supplement"],
        },
    )

    assert str(value.constraints.seed_urls[0]) == "https://example.org/must-check"
    assert str(value.constraints.source_pool_urls[0]) == "https://example.org/supplement"
