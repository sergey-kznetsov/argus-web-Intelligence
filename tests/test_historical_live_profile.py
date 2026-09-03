from __future__ import annotations

from argus.live_acceptance import acceptance_failures


_REQUIRED = {"historical_context", "historical_images", "public_mentions"}


def _overview(covered: set[str]) -> dict[str, object]:
    missing = _REQUIRED - covered
    return {
        "profile": "historical",
        "status": "completed" if not missing else "partial",
        "covered_intents": sorted(covered),
        "uncovered_intents": sorted(missing),
        "observation_count": max(1, len(covered)),
        "evidence_count": max(1, len(covered)),
        "runtime_terminal_status_version": "",
        "query_shape_violations": [],
        "research_supervisor": {"continue_research": bool(missing)} if missing else {},
    }


def test_historical_live_acceptance_requires_all_requested_outputs():
    assert acceptance_failures([_overview(_REQUIRED)]) == []

    failures = acceptance_failures(
        [_overview({"historical_context", "public_mentions"})]
    )
    assert failures == [
        "historical: required intents are not factually covered: historical_images"
    ]
