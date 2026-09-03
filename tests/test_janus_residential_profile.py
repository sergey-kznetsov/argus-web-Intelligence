from __future__ import annotations

from argus.live_acceptance import acceptance_failures, build_profile_request
from argus.web.profiles import web_test_profiles


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

    request = build_profile_request(
        "janus",
        profile,
        city="Пермь",
        address="Комсомольский проспект, 27",
    )
    assert set(request.intents) == _REQUIRED
    assert request.consumer == "janus.simulation"
    assert request.constraints.max_pages == 18
    assert request.constraints.max_depth == 2


def test_janus_live_acceptance_requires_both_residential_facts():
    assert acceptance_failures([_janus_overview(_REQUIRED)]) == []

    failures = acceptance_failures(
        [_janus_overview({"residential_population"})]
    )
    assert failures == [
        "janus: required residential intents are not factually covered: "
        "residential_premises_count"
    ]
