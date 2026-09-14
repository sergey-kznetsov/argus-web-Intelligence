from __future__ import annotations

from argus.live_acceptance import acceptance_failures, build_profile_request
from argus.web.profiles import web_test_profiles


_REQUIRED = {"residential_premises_count"}


def _janus_overview(covered: set[str]) -> dict[str, object]:
    return {
        "profile": "janus",
        "status": "completed",
        "covered_intents": sorted(covered),
        "uncovered_intents": sorted(_REQUIRED - covered),
        "observation_count": len(covered),
        "evidence_count": len(covered),
        "runtime_terminal_status_version": "",
        "query_shape_violations": [],
    }


def test_janus_simulation_uses_real_isolated_consumer_contract():
    profile = web_test_profiles()["janus"]

    assert set(profile["intents"]) == _REQUIRED
    assert profile["consumer"] == "janus.parking.potential.uds"
    assert profile["capability"] == "residential_facts"
    assert profile["requested_facts"] == ["residential_premises_count"]
    assert not any(str(intent).startswith("parking_") for intent in profile["intents"])

    request = build_profile_request(
        "janus",
        profile,
        city="Пермь",
        address="Комсомольский проспект, 27",
    )
    assert set(request.intents) == _REQUIRED
    assert request.consumer == "janus.parking.potential.uds"
    assert request.consumer_profile_version == 1
    assert request.capability == "residential_facts"
    assert request.requested_facts == ["residential_premises_count"]
    assert request.tool_pack_id == "janus.residential_facts"
    assert request.constraints.allowed_domains == ["dom.mingkh.ru"]
    assert request.constraints.max_pages == 1
    assert request.constraints.max_depth == 0


def test_janus_live_acceptance_requires_only_residential_premises_count():
    assert acceptance_failures([_janus_overview(_REQUIRED)]) == []

    failures = acceptance_failures([_janus_overview(set())])
    assert "janus: required residential intents are not factually covered: residential_premises_count" in failures
    assert not any("research supervisor never ran" in failure for failure in failures)
