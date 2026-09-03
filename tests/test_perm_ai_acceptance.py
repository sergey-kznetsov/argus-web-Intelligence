from scripts.perm_ai_acceptance import _acceptance_failures, _query_shape_violations


def base_overview() -> dict[str, object]:
    return {
        "profile": "kraken",
        "status": "completed",
        "covered_intents": ["complaints", "comments"],
        "uncovered_intents": ["incidents"],
        "observation_count": 2,
        "evidence_count": 2,
        "runtime_terminal_status_version": "evidence-aware-terminal-status/1",
        "runtime_covered_intents": ["complaints", "comments"],
        "runtime_uncovered_intents": ["incidents"],
        "research_supervisor": {"continue_research": True},
        "query_shape_violations": [],
    }


def test_live_acceptance_rejects_runtime_probe_coverage_disagreement():
    item = base_overview()
    item["runtime_uncovered_intents"] = ["incidents", "local_news"]

    failures = _acceptance_failures([item])

    assert "kraken: production runtime coverage disagrees with independent probe" in failures


def test_live_acceptance_accepts_matching_runtime_probe_coverage():
    failures = _acceptance_failures([base_overview()])

    assert failures == []


def test_live_acceptance_rejects_uncovered_gaps_when_supervisor_never_ran():
    item = base_overview()
    item["research_supervisor"] = {}

    failures = _acceptance_failures([item])

    assert "kraken: factual gaps remained but research supervisor never ran" in failures


def test_query_shape_check_detects_old_serialized_llm_plan_regression():
    checkpoint = {
        "queries": [
            "{'queries': ['complaints'], 'metadata': ['public']}",
            '"Пермь, Комсомольский проспект, 27" жалобы жителей',
        ],
        "adaptive_followup_queries": [
            "{'search_string': 'local_news', 'notes': ['wrong shape']}"
        ],
    }

    violations = _query_shape_violations(checkpoint)

    assert len(violations) == 2
    assert {item["reason"] for item in violations} == {"serialized_container"}


def test_query_shape_check_allows_normal_social_problem_queries():
    checkpoint = {
        "queries": ['"Пермь, Комсомольский проспект, 27" жалобы жителей'],
        "adaptive_followup_queries": [
            '"Пермь, Комсомольский проспект" проблемы жителей происшествия'
        ],
    }

    assert _query_shape_violations(checkpoint) == []
