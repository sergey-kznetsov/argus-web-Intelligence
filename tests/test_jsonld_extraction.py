from argus.extraction.jsonld import EmbeddedJsonLdExtractor


def test_jsonld_extracts_object_graph_and_list_entities():
    html = """
    <html><head>
      <script type="application/ld+json">
        {"@context":"https://schema.org","@type":"Organization","name":"UDS"}
      </script>
      <script type="application/ld+json; charset=utf-8">
        {"@context":"https://schema.org","@graph":[
          {"@type":"Place","name":"Объект 1"},
          {"@type":"Place","name":"Объект 2"}
        ]}
      </script>
      <script type="application/ld+json">
        [{"@type":"Article","headline":"Материал"}]
      </script>
    </head></html>
    """

    result = EmbeddedJsonLdExtractor().extract(html, "text/html")

    assert result.blocks_seen == 3
    assert result.blocks_invalid == 0
    assert [item.data.get("name") or item.data.get("headline") for item in result.entities] == [
        "UDS",
        "Объект 1",
        "Объект 2",
        "Материал",
    ]
    assert result.entities[0].data["@context"] == "https://schema.org"


def test_jsonld_invalid_and_oversized_blocks_are_skipped():
    extractor = EmbeddedJsonLdExtractor(max_block_chars=1_000)
    html = """
    <script type="application/ld+json">{invalid json</script>
    <script type="application/ld+json">%s</script>
    """ % ("x" * 1_001)

    result = extractor.extract(html, "text/html")

    assert result.entities == []
    assert result.blocks_seen == 2
    assert result.blocks_invalid == 1
    assert result.blocks_oversized == 1


def test_jsonld_entity_count_and_string_size_are_bounded():
    extractor = EmbeddedJsonLdExtractor(max_entities=2, max_string_chars=100)
    html = """
    <script type="application/ld+json">
      [
        {"@type":"Thing","name":"%s"},
        {"@type":"Thing","name":"second"},
        {"@type":"Thing","name":"third"}
      ]
    </script>
    """ % ("a" * 300)

    result = extractor.extract(html, "text/html")

    assert len(result.entities) == 2
    assert len(result.entities[0].data["name"]) == 100
    assert result.entities[1].data["name"] == "second"


def test_jsonld_is_not_parsed_from_non_html_content():
    result = EmbeddedJsonLdExtractor().extract(
        '<script type="application/ld+json">{"name":"x"}</script>',
        "application/json",
    )
    assert result.entities == []
    assert result.blocks_seen == 0
