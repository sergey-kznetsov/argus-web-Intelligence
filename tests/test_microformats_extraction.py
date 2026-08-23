from argus.extraction.microformats import extract_microformats


def test_h_entry_extracts_explicit_properties_without_nested_h_card_leakage():
    html = """
    <article class="h-entry">
      <h1 class="p-name">Новость района</h1>
      <a class="u-url" href="/news/1">permalink</a>
      <time class="dt-published" datetime="2026-08-23T09:00:00+04:00">today</time>
      <div class="p-author h-card">
        <span class="p-name">Автор внутри h-card</span>
        <a class="u-url" href="/authors/1">author url</a>
      </div>
      <div class="e-content"><p>Фактический текст новости</p></div>
      <a class="p-category" href="/tags/city">город</a>
    </article>
    """

    result = extract_microformats(
        html,
        content_type="text/html",
        base_url="https://example.com/root",
    )

    assert result.roots_seen == 1
    assert result.roots_skipped == 0
    assert len(result.items) == 1
    item = result.items[0]
    assert item.kind == "h-entry"
    assert item.title == "Новость района"
    assert item.source_url == "https://example.com/news/1"
    assert item.entity_id == "https://example.com/news/1"
    assert item.text == "Фактический текст новости"
    assert item.published_at is not None
    assert item.properties["p-name"] == ["Новость района"]
    assert item.properties["p-author"] == ["Автор внутри h-card author url"]
    assert item.properties["p-category"] == ["город"]
    assert item.properties["u-url"] == ["https://example.com/news/1"]


def test_h_review_preserves_review_item_rating_author_and_content():
    html = """
    <div class="h-review">
      <h2 class="p-name">Отзыв о центре</h2>
      <a class="p-item h-item" href="https://example.com/place">Детский центр</a>
      <span class="p-author">Иван</span>
      <data class="p-rating" value="4.5">4.5</data>
      <data class="p-best" value="5">5</data>
      <data class="p-worst" value="0">0</data>
      <time class="dt-published" datetime="2026-08-22T18:00:00+04:00"></time>
      <div class="e-content"><p>Подробный отзыв</p></div>
      <a class="u-url" href="/reviews/42">review url</a>
    </div>
    """

    result = extract_microformats(
        html,
        content_type="text/html",
        base_url="https://example.com/page",
    )

    item = result.items[0]
    assert item.kind == "h-review"
    assert item.source_url == "https://example.com/reviews/42"
    assert item.title == "Отзыв о центре"
    assert item.text == "Подробный отзыв"
    assert item.properties["p-item"] == ["Детский центр"]
    assert item.properties["p-author"] == ["Иван"]
    assert item.properties["p-rating"] == ["4.5"]
    assert item.properties["p-best"] == ["5"]
    assert item.properties["p-worst"] == ["0"]


def test_microformats_rejects_unsafe_urls_and_skips_empty_roots():
    html = """
    <article class="h-entry">
      <a class="u-url" href="javascript:alert(1)">bad</a>
      <div class="e-content">Still valid factual content</div>
    </article>
    <article class="h-entry"></article>
    """

    result = extract_microformats(
        html,
        content_type="text/html",
        base_url="https://example.com/root",
    )

    assert result.roots_seen == 2
    assert result.roots_skipped == 1
    assert len(result.items) == 1
    assert result.items[0].source_url == "https://example.com/root"
    assert "u-url" not in result.items[0].properties


def test_microformats_bounds_roots_values_and_html():
    html = """
    <div class="h-entry">
      <span class="p-name">abcdefghijk</span>
      <span class="p-category">one</span>
      <span class="p-category">two</span>
      <span class="p-category">three</span>
      <div class="e-content"><b>long content here</b></div>
    </div>
    <div class="h-entry"><span class="p-name">second</span></div>
    """

    result = extract_microformats(
        html,
        content_type="text/html",
        base_url="https://example.com/",
        max_roots=1,
        max_values_per_property=2,
        max_value_chars=5,
        max_html_chars=6,
    )

    assert result.truncated is True
    assert result.roots_seen == 2
    assert len(result.items) == 1
    item = result.items[0]
    assert item.title == "abcde"
    assert item.properties["p-category"] == ["one", "two"]
    content = item.properties["e-content"][0]
    assert isinstance(content, dict)
    assert content["value"] == "long "
    assert len(content["html"]) <= 6


def test_microformats_ignores_non_html_and_unmarked_content():
    assert extract_microformats(
        '<div class="h-entry"><span class="p-name">X</span></div>',
        content_type="application/json",
        base_url="https://example.com/",
    ).items == []
    assert extract_microformats(
        "<html><body>Plain discussion text</body></html>",
        content_type="text/html",
        base_url="https://example.com/",
    ).items == []
