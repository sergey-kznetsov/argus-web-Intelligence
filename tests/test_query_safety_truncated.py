from argus.contracts.models import CollectionRequest
from argus.research.query_safety import sanitize_research_queries


def test_truncated_serialized_llm_container_is_rejected_fail_closed():
    request = CollectionRequest(
        consumer="historical-query-safety-test",
        analysis_id="historical-query-safety-test",
        territory={"city": "Пермь", "address": "Комсомольский проспект, 27"},
        intents=["historical_context", "historical_images", "public_mentions"],
    )
    malformed = (
        "{'queries': ['search for information about historical context at the location', "
        "'find historical images'], 'notes': ['truncated before the container can close'"
    )

    queries = sanitize_research_queries(
        [malformed],
        request,
        max_queries=8,
    )

    assert queries == []


def test_plain_query_text_is_not_rejected_by_container_guard():
    request = CollectionRequest(
        consumer="historical-query-safety-test",
        analysis_id="historical-query-safety-test-plain",
        territory={"city": "Пермь", "address": "Комсомольский проспект, 27"},
        intents=["historical_context"],
    )

    queries = sanitize_research_queries(
        ["история строительства здания"],
        request,
        max_queries=2,
    )

    assert queries == ['"Пермь, Комсомольский проспект, 27" история строительства здания']
