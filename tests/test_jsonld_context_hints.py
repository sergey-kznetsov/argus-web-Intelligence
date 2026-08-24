from argus.extraction.jsonld import EmbeddedJsonLdExtractor


def test_jsonld_graph_entities_inherit_root_schema_context_hint():
    html = """
    <script type="application/ld+json">
      {
        "@context": "https://schema.org",
        "@graph": [
          {"@type": "Review", "name": "Отзыв"},
          {"@type": "NewsArticle", "headline": "Новость"}
        ]
      }
    </script>
    """

    result = EmbeddedJsonLdExtractor().extract(html, "text/html")

    assert len(result.entities) == 2
    assert result.entities[0].context_hints == ("https://schema.org",)
    assert result.entities[1].context_hints == ("https://schema.org",)


def test_entity_context_overrides_inherited_context_hint_without_resolution():
    html = """
    <script type="application/ld+json">
      {
        "@context": "https://schema.org",
        "@graph": [
          {
            "@context": {"@vocab": "https://example.org/vocab/"},
            "@type": "Review",
            "name": "External vocabulary"
          }
        ]
      }
    </script>
    """

    result = EmbeddedJsonLdExtractor().extract(html, "text/html")

    assert len(result.entities) == 1
    assert result.entities[0].context_hints == ("https://example.org/vocab/",)


def test_context_hint_count_and_length_are_bounded():
    extractor = EmbeddedJsonLdExtractor(
        max_context_hints=2,
        max_context_hint_chars=20,
    )
    html = """
    <script type="application/ld+json">
      {
        "@context": [
          "https://schema.org/very-long-context-value",
          "https://example.org/second",
          "https://example.org/third"
        ],
        "@type": "Thing",
        "name": "X"
      }
    </script>
    """

    result = extractor.extract(html, "text/html")

    assert len(result.entities) == 1
    assert len(result.entities[0].context_hints) == 2
    assert all(len(value) <= 20 for value in result.entities[0].context_hints)
