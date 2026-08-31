from argus.contracts.models import Observation
from argus.sources.schema_web import SchemaAwareSemanticWebAdapter


def test_schema_post_uses_source_declared_body_and_publication_time():
    observation = Observation(
        collection_id="collection",
        analysis_id="analysis",
        consumer="test",
        source="generic_web",
        source_kind="web_page",
        url="https://example.com/post/1",
        entity_type="post",
        content_hash="hash",
    )
    values = {
        "articleBody": "Жители сообщили о перекрытии двора.",
        "datePublished": "2026-08-30T12:00:00+03:00",
    }
    adapter = object.__new__(SchemaAwareSemanticWebAdapter)

    adapter._apply_schema_fields(
        observation,
        [],
        entity_type="post",
        value_getter=values.get,
    )

    assert observation.text == "Жители сообщили о перекрытии двора."
    assert observation.published_at is not None
    assert observation.published_at.isoformat() == "2026-08-30T12:00:00+03:00"
    assert observation.provenance["schema_field_normalization"] == {
        "text_field": "articleBody",
        "published_at_field": "datePublished",
        "source_declared_only": True,
    }
