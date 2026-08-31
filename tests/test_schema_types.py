from argus.normalization.schema_types import classify_schema_entity


def test_classifies_full_schema_org_urls_without_context():
    assert classify_schema_entity("https://schema.org/Review") == ("review", ["Review"])
    assert classify_schema_entity("http://schema.org/NewsArticle") == (
        "publication",
        ["NewsArticle"],
    )
    assert classify_schema_entity("https://schema.org/SocialMediaPosting") == (
        "post",
        ["SocialMediaPosting"],
    )
    assert classify_schema_entity("https://schema.org/GovernmentOrganization") == (
        "organization",
        ["GovernmentOrganization"],
    )
    assert classify_schema_entity("https://schema.org/EducationEvent") == (
        "event",
        ["EducationEvent"],
    )


def test_simple_type_requires_explicit_schema_context():
    assert classify_schema_entity("Review") == ("structured_entity", [])
    assert classify_schema_entity(
        "Review",
        context_hints=["https://schema.org"],
    ) == ("review", ["Review"])


def test_unknown_vocabularies_are_not_reinterpreted_as_schema_org():
    assert classify_schema_entity("https://example.org/Review") == (
        "structured_entity",
        [],
    )
    assert classify_schema_entity(
        "example:Review",
        context_hints=["https://schema.org"],
    ) == ("structured_entity", [])


def test_known_schema_type_can_still_remain_generic_when_no_category_mapping_exists():
    assert classify_schema_entity("https://schema.org/Thing") == (
        "structured_entity",
        ["Thing"],
    )


def test_multiple_types_use_specific_factual_category_precedence():
    assert classify_schema_entity(
        ["https://schema.org/CreativeWork", "https://schema.org/Review"]
    ) == ("review", ["CreativeWork", "Review"])
    assert classify_schema_entity(
        ["Article", "NewsArticle"],
        context_hints=["https://schema.org/"],
    ) == ("publication", ["Article", "NewsArticle"])
    assert classify_schema_entity(
        ["Article", "DiscussionForumPosting"],
        context_hints=["https://schema.org/"],
    ) == ("post", ["Article", "DiscussionForumPosting"])
