from __future__ import annotations

from argus.web.profiles import web_test_profiles
from scripts.perm_ai_acceptance import _acceptance_failures, _request


_REQUIRED = {"residential_population", "residential_premises_count"}


def _janus_overview(covered: set[str]) -> dict[str, object]:
    return {
        "profile": "janus",
        "status": "completed",
        "covered_intents": sorted(covered),
        "uncovered_intents": [],
        "observation_count": len(covered),
        "evidence_count": len(covered),
        "runtime_terminal_status_version": "",
        "query_shape_violations": [],
    }


def test_janus_simulation_requests_only_residential_building_facts():
    profile = web_test_profiles()["janus"]

    assert set(profile["intents"]) == _REQUIRED
    assert not any(str(intent).startswith("parking_") for intent in profile["intents"])

    request = _request("janus", profile)
    assert set(request.intents) == _REQUIRED
    assert request.consumer == "janus.simulation"


def test_janus_live_acceptance_requires_both_residential_facts():
    assert _acceptance_failures([_janus_overview(_REQUIRED)]) == []

    failures = _acceptance_failures(
        [_janus_overview({"residential_population"})]
    )
    assert failures == [
        "janus: required residential intents are not factually covered: "
        "residential_premises_count"
    ]
