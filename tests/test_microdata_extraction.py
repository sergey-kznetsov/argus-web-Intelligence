from argus.extraction.microdata import extract_microdata


def test_extracts_explicit_microdata_values_and_date():
    html = """
    <article itemscope itemtype="https://schema.org/NewsArticle" itemid="/news/1">
      <meta itemprop="headline" content="Новая школа" />
      <p itemprop="description">Открылась новая школа</p>
      <time itemprop="datePublished" datetime="2026-08-20T10:00:00+04:00"></time>
      <a itemprop="url" href="/news/1">Материал</a>
    </article>
    """

    result = extract_microdata(
        html,
        content_type="text/html",
        base_url="https://example.com/page",
    )

    assert result.items_seen == 1
    assert result.items_skipped == 0
    assert result.truncated is False
    item = result.items[0]
    assert item.item_types == ["https://schema.org/NewsArticle"]
    assert item.item_id == "https://example.com/news/1"
    assert item.entity_id == "https://example.com/news/1"
    assert item.title == "Новая школа"
    assert item.text == "Открылась новая школа"
    assert item.properties["url"] == ["https://example.com/news/1"]
    assert item.published_at is not None
    assert item.published_at.isoformat() == "2026-08-20T10:00:00+04:00"


def test_nested_items_are_references_for_parent_and_separate_items():
    html = """
    <article itemscope itemtype="https://schema.org/Article" itemid="https://example.com/a">
      <span itemprop="headline">Материал</span>
      <span itemprop="author" itemscope itemtype="https://schema.org/Person" itemid="/people/1">
        <span itemprop="name">Иван Иванов</span>
      </span>
    </article>
    """

    result = extract_microdata(
        html,
        content_type="text/html",
        base_url="https://example.com/a",
    )

    assert len(result.items) == 2
    article, person = result.items
    assert article.properties["author"] == [
        {
            "itemid": "https://example.com/people/1",
            "itemtype": ["https://schema.org/Person"],
        }
    ]
    assert "name" not in article.properties
    assert person.item_id == "https://example.com/people/1"
    assert person.properties["name"] == ["Иван Иванов"]
    assert person.title == "Иван Иванов"


def test_itemref_item_is_skipped_instead_of_partial_extraction():
    html = """
    <div id="external"><span itemprop="name">Referenced name</span></div>
    <div itemscope itemtype="https://schema.org/Thing" itemref="external">
      <span itemprop="description">Local description</span>
    </div>
    """

    result = extract_microdata(
        html,
        content_type="text/html",
        base_url="https://example.com/",
    )

    assert result.items_seen == 1
    assert result.itemref_skipped == 1
    assert result.items_skipped == 1
    assert result.items == []


def test_unsafe_url_property_is_not_emitted_and_marks_item_incomplete():
    html = """
    <div itemscope itemtype="https://schema.org/Thing">
      <span itemprop="name">Safe name</span>
      <a itemprop="url" href="javascript:alert(1)">Unsafe</a>
    </div>
    """

    result = extract_microdata(
        html,
        content_type="text/html",
        base_url="https://example.com/",
    )

    assert len(result.items) == 1
    assert result.items[0].properties["name"] == ["Safe name"]
    assert "url" not in result.items[0].properties
    assert result.items[0].truncated is True
    assert result.truncated is True


def test_long_text_value_is_clipped_and_marks_item_truncated():
    html = """
    <div itemscope itemtype="https://schema.org/Thing">
      <span itemprop="name">abcdefghij</span>
    </div>
    """

    result = extract_microdata(
        html,
        content_type="text/html",
        base_url="https://example.com/",
        max_value_chars=5,
    )

    assert result.truncated is True
    item = result.items[0]
    assert item.truncated is True
    assert item.properties["name"] == ["abcde"]


def test_overlong_property_name_is_not_rewritten_as_another_property():
    long_name = "x" * 129
    html = f"""
    <div itemscope itemtype="https://schema.org/Thing">
      <span itemprop="name">Safe</span>
      <span itemprop="{long_name}">Do not rename me</span>
    </div>
    """

    result = extract_microdata(
        html,
        content_type="text/html",
        base_url="https://example.com/",
    )

    item = result.items[0]
    assert item.properties == {"name": ["Safe"]}
    assert item.truncated is True
    assert result.truncated is True


def test_property_and_value_limits_are_reported():
    html = """
    <div itemscope>
      <span itemprop="a">1</span>
      <span itemprop="a">2</span>
      <span itemprop="b">3</span>
      <span itemprop="c">4</span>
    </div>
    """

    result = extract_microdata(
        html,
        content_type="text/html",
        base_url="https://example.com/",
        max_properties_per_item=2,
        max_values_per_property=1,
    )

    assert result.truncated is True
    assert len(result.items) == 1
    item = result.items[0]
    assert item.truncated is True
    assert item.properties == {"a": ["1"], "b": ["3"]}


def test_scan_and_item_limits_are_bounded():
    html = "".join(
        f'<div itemscope itemid="https://example.com/{index}"><span itemprop="name">{index}</span></div>'
        for index in range(5)
    )

    result = extract_microdata(
        html,
        content_type="text/html",
        base_url="https://example.com/",
        max_items=2,
    )

    assert result.items_seen == 5
    assert len(result.items) == 2
    assert result.truncated is True


def test_non_html_content_is_ignored():
    result = extract_microdata(
        '<div itemscope><span itemprop="name">X</span></div>',
        content_type="application/json",
        base_url="https://example.com/",
    )

    assert result.items_seen == 0
    assert result.items == []
