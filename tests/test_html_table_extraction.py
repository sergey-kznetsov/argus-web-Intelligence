from argus.extraction.html_tables import extract_html_tables


def test_extracts_simple_semantic_table_with_headers_and_caption():
    html = """
    <table>
      <caption>Объекты образования</caption>
      <thead><tr><th>Название</th><th>Адрес</th></tr></thead>
      <tbody>
        <tr><td>Школа 1</td><td>ул. Ленина, 1</td></tr>
        <tr><td>Школа 2</td><td>ул. Мира, 2</td></tr>
      </tbody>
    </table>
    """

    result = extract_html_tables(html, content_type="text/html")

    assert result.tables_seen == 1
    assert result.layout_skipped == 0
    assert result.complex_skipped == 0
    assert result.truncated is False
    assert len(result.tables) == 1
    table = result.tables[0]
    assert table.caption == "Объекты образования"
    assert table.headers == ["Название", "Адрес"]
    assert table.rows == [
        ["Школа 1", "ул. Ленина, 1"],
        ["Школа 2", "ул. Мира, 2"],
    ]
    assert table.column_count == 2
    assert table.truncated is False


def test_skips_layout_table_and_unmarked_table():
    html = """
    <table role="presentation"><tr><th>Layout</th></tr></table>
    <table><tr><td>A</td><td>B</td></tr></table>
    """

    result = extract_html_tables(html, content_type="text/html")

    assert result.tables_seen == 2
    assert result.layout_skipped == 2
    assert result.tables == []


def test_skips_complex_rowspan_and_colspan_tables():
    html = """
    <table><tr><th colspan="2">Header</th></tr><tr><td>A</td><td>B</td></tr></table>
    <table><tr><th>A</th><th>B</th></tr><tr><td rowspan="2">X</td><td>Y</td></tr></table>
    """

    result = extract_html_tables(html, content_type="text/html")

    assert result.tables_seen == 2
    assert result.complex_skipped == 2
    assert result.tables == []


def test_excludes_nested_table_text_from_outer_cells():
    html = """
    <table>
      <tr><th>Name</th><th>Value</th></tr>
      <tr>
        <td>Outer</td>
        <td>Before <table><tr><th>Nested</th></tr><tr><td>Secret</td></tr></table> After</td>
      </tr>
    </table>
    """

    result = extract_html_tables(html, content_type="text/html")

    assert result.tables_seen == 2
    assert len(result.tables) == 2
    outer = result.tables[0]
    assert outer.rows == [["Outer", "Before After"]]
    nested = result.tables[1]
    assert nested.headers == ["Nested"]
    assert nested.rows == [["Secret"]]


def test_marks_cell_column_and_row_truncation():
    html = """
    <table aria-label="Very long caption">
      <tr><th>Column 1</th><th>Column 2</th><th>Column 3</th></tr>
      <tr><td>abcdefghij</td><td>B</td><td>C</td></tr>
      <tr><td>second</td><td>B2</td><td>C2</td></tr>
    </table>
    """

    result = extract_html_tables(
        html,
        content_type="text/html",
        max_rows_per_table=2,
        max_columns=2,
        max_cell_chars=5,
    )

    assert result.truncated is True
    table = result.tables[0]
    assert table.truncated is True
    assert table.caption == "Very "
    assert table.headers == ["Colum", "Colum"]
    assert table.rows == [["abcde", "B"]]
    assert table.column_count == 2


def test_total_row_budget_truncates_later_table_without_overflow():
    html = """
    <table><tr><th>A</th></tr><tr><td>1</td></tr><tr><td>2</td></tr></table>
    <table><tr><th>B</th></tr><tr><td>3</td></tr><tr><td>4</td></tr></table>
    """

    result = extract_html_tables(
        html,
        content_type="text/html",
        max_total_rows=3,
    )

    assert result.truncated is True
    assert len(result.tables) == 2
    assert result.tables[0].rows == [["1"], ["2"]]
    assert result.tables[1].rows == [["3"]]
    assert result.tables[1].truncated is True


def test_non_html_content_is_ignored():
    result = extract_html_tables(
        "<table><tr><th>A</th></tr><tr><td>1</td></tr></table>",
        content_type="application/json",
    )

    assert result.tables_seen == 0
    assert result.tables == []
