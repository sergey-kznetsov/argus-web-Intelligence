from argus.normalization.identity import stable_evidence_id, stable_observation_id


def test_observation_identity_is_stable_within_collection():
    first = stable_observation_id(
        collection_id="collection-1",
        source_id="generic_web",
        entity_type="document",
        source_url="https://example.com/page",
        content_hash="abc",
    )
    second = stable_observation_id(
        collection_id="collection-1",
        source_id="generic_web",
        entity_type="document",
        source_url="https://example.com/page",
        content_hash="abc",
    )
    changed = stable_observation_id(
        collection_id="collection-1",
        source_id="generic_web",
        entity_type="document",
        source_url="https://example.com/page",
        content_hash="def",
    )
    other_collection = stable_observation_id(
        collection_id="collection-2",
        source_id="generic_web",
        entity_type="document",
        source_url="https://example.com/page",
        content_hash="abc",
    )

    assert first == second
    assert changed != first
    assert other_collection != first


def test_evidence_identity_depends_on_content():
    first = stable_evidence_id("observation", "document", "https://example.com", "text")
    second = stable_evidence_id("observation", "document", "https://example.com", "text")
    changed = stable_evidence_id("observation", "document", "https://example.com", "other")

    assert first == second
    assert changed != first
