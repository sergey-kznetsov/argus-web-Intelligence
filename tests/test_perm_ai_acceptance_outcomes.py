from __future__ import annotations

from argus.live_acceptance import acceptance_failures, mandatory_janus_source_blocked


def test_direct_mingkh_access_challenge_is_the_only_legacy_source_block_exception():
    error_details = [{"code": "MINGKH_ACCESS_CHALLENGE", "source_id": "mingkh_residential"}]
    assert mandatory_janus_source_blocked(
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
        "uncovered_intents": ["residential_premises_count"],
        "observation_count": 0,
        "evidence_count": 0,
        "research_supervisor": {},
        "query_shape_violations": [],
    }
    assert acceptance_failures([overview]) == []


def test_site_discovery_block_is_not_a_janus_exception_anymore():
    for code in ("SOURCE_ROBOTS_ACCESS_BLOCKED", "SOURCE_ROBOTS_UNREACHABLE"):
        assert mandatory_janus_source_blocked(
            profile_id="janus",
            status="blocked",
            observation_count=0,
            evidence_count=0,
            error_details=[{"code": code, "source_id": "site_discovery"}],
        ) is False


def test_source_block_requires_janus_blocked_empty_result_and_mingkh_source():
    error_details = [{"code": "MINGKH_ACCESS_CHALLENGE", "source_id": "mingkh_residential"}]

    assert mandatory_janus_source_blocked(
        profile_id="kraken",
        status="blocked",
        observation_count=0,
        evidence_count=0,
        error_details=error_details,
    ) is False
    assert mandatory_janus_source_blocked(
        profile_id="janus",
        status="partial",
        observation_count=0,
        evidence_count=0,
        error_details=error_details,
    ) is False
    assert mandatory_janus_source_blocked(
        profile_id="janus",
        status="blocked",
        observation_count=1,
        evidence_count=0,
        error_details=error_details,
    ) is False
    assert mandatory_janus_source_blocked(
        profile_id="janus",
        status="blocked",
        observation_count=0,
        evidence_count=0,
        error_details=[{"code": "MINGKH_ACCESS_CHALLENGE", "source_id": "generic_web"}],
    ) is False


def test_janus_missing_facts_without_explicit_source_block_still_fails():
    overview = {
        "profile": "janus",
        "status": "failed",
        "mandatory_source_blocked": False,
        "covered_intents": [],
        "uncovered_intents": ["residential_premises_count"],
        "observation_count": 0,
        "evidence_count": 0,
        "research_supervisor": {},
        "query_shape_violations": [],
    }
    failures = acceptance_failures([overview])
    assert any("terminal status" in item for item in failures)
    assert any("required residential intents" in item for item in failures)
