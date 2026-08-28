from __future__ import annotations

import pytest

from scripts.perm_ai_acceptance import (
    _acceptance_failures,
    _mandatory_janus_source_blocked,
)


@pytest.mark.parametrize(
    ("code", "source_id"),
    [
        ("MINGKH_ACCESS_CHALLENGE", "mingkh_residential"),
        ("SOURCE_ROBOTS_ACCESS_BLOCKED", "site_discovery"),
        ("SOURCE_ROBOTS_UNREACHABLE", "site_discovery"),
    ],
)
def test_explicit_mandatory_source_block_is_handled_not_code_failure(
    code: str,
    source_id: str,
):
    error_details = [{"code": code, "source_id": source_id}]
    assert _mandatory_janus_source_blocked(
        profile_id="janus",
        status="blocked",
        observation_count=0,
        evidence_count=0,
        error_details=error_details,
    ) is True

    overview = {
        "profile": "janus",
        "status": "blocked",
        "mandatory_source_blocked": True,
        "covered_intents": [],
        "uncovered_intents": [
            "residential_population",
            "residential_premises_count",
        ],
        "observation_count": 0,
        "evidence_count": 0,
        "research_supervisor": {},
        "query_shape_violations": [],
    }
    assert _acceptance_failures([overview]) == []


def test_janus_missing_facts_without_explicit_source_block_still_fails():
    overview = {
        "profile": "janus",
        "status": "failed",
        "mandatory_source_blocked": False,
        "covered_intents": [],
        "uncovered_intents": [
            "residential_population",
            "residential_premises_count",
        ],
        "observation_count": 0,
        "evidence_count": 0,
        "research_supervisor": {},
        "query_shape_violations": [],
    }
    failures = _acceptance_failures([overview])
    assert any("terminal status" in item for item in failures)
    assert any("required residential intents" in item for item in failures)
