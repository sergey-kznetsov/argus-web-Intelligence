from argus.extraction.page_metadata import extract_page_metadata


def test_page_metadata_extracts_declared_fields_without_inference():
    html = """
    <html><head>
      <link rel="canonical" href="/news/item-1">
      <meta property="og:title" content="Первый заголовок">
      <meta property="og:title" content="Второй заголовок">
      <meta property="og:type" content="article">
      <meta property="og:url" content="/news/item-1">
      <meta property="og:description" content="Описание материала">
      <meta property="og:site_name" content="Городские новости">
      <meta property="article:published_time" content="2026-08-23T10:30:00+04:00">
      <meta property="article:modified_time" content="2026-08-23T11:00:00+04:00">
      <meta property="article:author" content="Автор 1">
      <meta property="article:author" content="Автор 2">
      <meta property="article:tag" content="город">
      <meta name="description" content="HTML description">
      <meta name="DC.creator" content="Редакция">
      <meta name="DCTERMS.created" content="2026-08-20">
      <meta name="DC.date" content="2026-08">
    </head><body>Body</body></html>
    """

    result = extract_page_metadata(
        html,
        content_type="text/html; charset=utf-8",
        base_url="https://example.com/root",
    )

    assert result.canonical_url == "https://example.com/news/item-1"
    assert result.fields["og_title"] == "Первый заголовок"
    assert result.fields["og_type"] == "article"
    assert result.fields["og_url"] == "https://example.com/news/item-1"
    assert result.fields["article_authors"] == ["Автор 1", "Автор 2"]
    assert result.fields["article_tags"] == ["город"]
    assert result.fields["dc_creators"] == ["Редакция"]
    assert result.fields["dcterms_created"] == "2026-08-20"
    assert result.fields["dc_date"] == "2026-08"
    assert result.published_at is not None
    assert result.published_at.isoformat() == "2026-08-23T10:30:00+04:00"


def test_page_metadata_rejects_unsafe_declared_urls_and_non_html():
    html = """
    <html><head>
      <link rel="canonical" href="javascript:alert(1)">
      <meta property="og:url" content="https://user:pass@example.com/private">
      <meta property="og:title" content="Title">
    </head></html>
    """

    result = extract_page_metadata(
        html,
        content_type="text/html",
        base_url="https://example.com/page",
    )
    assert result.canonical_url is None
    assert "canonical_url" not in result.fields
    assert "og_url" not in result.fields
    assert result.fields["og_title"] == "Title"

    not_html = extract_page_metadata(
        html,
        content_type="application/json",
        base_url="https://example.com/page",
    )
    assert not_html.fields == {}


def test_page_metadata_bounds_scan_values_and_arrays():
    html = """
    <html><head>
      <meta property="og:title" content="abcdefghijk">
      <meta property="article:tag" content="one">
      <meta property="article:tag" content="two">
      <meta property="article:tag" content="three">
    </head><body>{}</body></html>
    """.format("x" * 500)

    result = extract_page_metadata(
        html,
        content_type="text/html",
        base_url="https://example.com/page",
        max_scan_chars=200,
        max_value_chars=5,
        max_values_per_field=2,
    )

    assert result.truncated is True
    assert result.fields["og_title"] == "abcde"
    tags = result.fields.get("article_tags", [])
    assert isinstance(tags, list)
    assert len(tags) <= 2


def test_dublin_core_date_is_not_relabelled_as_publication_time():
    result = extract_page_metadata(
        '<html><head><meta name="DC.date" content="2026-08-23"></head></html>',
        content_type="text/html",
        base_url="https://example.com/page",
    )

    assert result.fields["dc_date"] == "2026-08-23"
    assert result.published_at is None
