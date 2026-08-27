from scripts.perm_ai_acceptance import _acceptance_failures, _query_shape_violations


def base_overview() -> dict[str, object]:
    return {
        "profile": "kraken",
        "status": "completed",
        "covered_intents": ["reviews", "public_mentions"],
        "uncovered_intents": ["complaints"],
        "observation_count": 2,
        "evidence_count": 2,
        "runtime_terminal_status_version": "evidence-aware-terminal-status/1",
        "runtime_covered_intents": ["reviews", "public_mentions"],
        "runtime_uncovered_intents": ["complaints"],
        "query_shape_violations": [],
    }


def test_live_acceptance_rejects_runtime_probe_coverage_disagreement():
    item = base_overview()
    item["runtime_uncovered_intents"] = ["complaints", "incidents"]

    failures = _acceptance_failures([item])

    assert "kraken: production runtime coverage disagrees with independent probe" in failures


def test_live_acceptance_accepts_matching_runtime_probe_coverage():
    failures = _acceptance_failures([base_overview()])

    assert failures == []


def test_query_shape_check_detects_old_serialized_llm_plan_regression():
    checkpoint = {
        "queries": [
            "{'queries': ['public_mentions'], 'metadata': ['public']}",
            '"Пермь, Комсомольский проспект, 27" отзывы',
        ],
        "adaptive_followup_queries": [
            "{'search_string': 'local_news', 'notes': ['wrong shape']}"
        ],
    }

    violations = _query_shape_violations(checkpoint)

    assert len(violations) == 2
    assert {item["reason"] for item in violations} == {"serialized_container"}


def test_query_shape_check_allows_normal_site_and_local_queries():
    checkpoint = {
        "queries": ['"Пермь, Комсомольский проспект, 27" отзывы'],
        "curated_public_map_queries": [
            'site:2gis.ru "Пермь, Комсомольский проспект, 27" отзывы жалобы'
        ],
    }

    assert _query_shape_violations(checkpoint) == []
