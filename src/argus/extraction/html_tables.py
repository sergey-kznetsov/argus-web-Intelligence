from __future__ import annotations

from dataclasses import dataclass, field

from bs4 import BeautifulSoup, Tag


@dataclass(slots=True)
class HtmlTable:
    index: int
    caption: str | None
    headers: list[str]
    rows: list[list[str]]
    column_count: int
    truncated: bool = False


@dataclass(slots=True)
class HtmlTableExtraction:
    tables: list[HtmlTable] = field(default_factory=list)
    tables_seen: int = 0
    layout_skipped: int = 0
    complex_skipped: int = 0
    empty_skipped: int = 0
    truncated: bool = False
    extractor_version: str = "html-table/1"


def extract_html_tables(
    html: str,
    *,
    content_type: str | None,
    max_scan_chars: int = 1_000_000,
    max_tables: int = 20,
    max_rows_per_table: int = 200,
    max_total_rows: int = 1_000,
    max_columns: int = 50,
    max_cell_chars: int = 5_000,
) -> HtmlTableExtraction:
    """Extract only clearly semantic, simple HTML data tables with bounded shape.

    Complex span grids are deliberately rejected rather than flattened incorrectly.
    Any configured limit that clips extracted source data is surfaced through the
    table-level and extraction-level ``truncated`` flags.
    """

    extraction = HtmlTableExtraction()
    if content_type and "html" not in content_type.casefold():
        return extraction
    scan_limit = max(1, int(max_scan_chars))
    table_limit = max(1, int(max_tables))
    row_limit = max(1, int(max_rows_per_table))
    total_row_limit = max(1, int(max_total_rows))
    column_limit = max(1, int(max_columns))
    cell_limit = max(1, int(max_cell_chars))
    source = html[:scan_limit]
    extraction.truncated = len(html) > scan_limit
    soup = BeautifulSoup(source, "html.parser")
    tables = [table for table in soup.find_all("table") if isinstance(table, Tag)]
    extraction.tables_seen = len(tables)
    if len(tables) > table_limit:
        extraction.truncated = True

    total_rows = 0
    for index, table in enumerate(tables[:table_limit]):
        if total_rows >= total_row_limit:
            extraction.truncated = True
            break
        if _looks_like_layout(table) or not _looks_like_data(table):
            extraction.layout_skipped += 1
            continue

        rows = _direct_table_rows(table)
        if not rows:
            extraction.empty_skipped += 1
            continue
        if _has_complex_spans(rows):
            extraction.complex_skipped += 1
            continue

        first_cells = _row_cells(rows[0])
        first_is_header = bool(first_cells) and all(cell.name == "th" for cell in first_cells)
        normalized_rows: list[list[str]] = []
        table_truncated = False
        raw_rows = rows[:row_limit]
        if len(rows) > row_limit:
            table_truncated = True

        for row in raw_rows:
            cells = _row_cells(row)
            if not cells:
                continue
            if len(cells) > column_limit:
                table_truncated = True
            values: list[str] = []
            for cell in cells[:column_limit]:
                value, value_truncated = _cell_text(cell, cell_limit)
                values.append(value)
                table_truncated = table_truncated or value_truncated
            normalized_rows.append(values)

        if not normalized_rows:
            extraction.empty_skipped += 1
            continue

        headers: list[str] = []
        data_rows = normalized_rows
        if first_is_header:
            headers = normalized_rows[0]
            data_rows = normalized_rows[1:]

        remaining_total = total_row_limit - total_rows
        if len(data_rows) > remaining_total:
            data_rows = data_rows[:remaining_total]
            table_truncated = True

        if not data_rows and not headers:
            extraction.empty_skipped += 1
            continue

        column_count = max(
            [len(headers), *(len(row) for row in data_rows)],
            default=0,
        )
        caption_tag = table.find("caption", recursive=False)
        caption: str | None = None
        if isinstance(caption_tag, Tag):
            caption, caption_truncated = _cell_text(caption_tag, cell_limit)
            table_truncated = table_truncated or caption_truncated
        if not caption:
            caption, caption_truncated = _bounded_text(table.get("aria-label"), cell_limit)
            table_truncated = table_truncated or caption_truncated

        extraction.tables.append(
            HtmlTable(
                index=index,
                caption=caption,
                headers=headers,
                rows=data_rows,
                column_count=column_count,
                truncated=table_truncated,
            )
        )
        total_rows += len(data_rows)
        if table_truncated:
            extraction.truncated = True
    return extraction


def _looks_like_layout(table: Tag) -> bool:
    role = str(table.get("role", "")).strip().casefold()
    return role in {"presentation", "none"}


def _looks_like_data(table: Tag) -> bool:
    if table.find("caption", recursive=False) is not None:
        return True
    if table.find("thead") is not None or table.find("th") is not None:
        return True
    role = str(table.get("role", "")).strip().casefold()
    if role in {"table", "grid", "treegrid"}:
        return True
    if table.get("aria-label") or table.get("aria-labelledby"):
        return True
    return False


def _direct_table_rows(table: Tag) -> list[Tag]:
    rows: list[Tag] = []
    for row in table.find_all("tr"):
        if not isinstance(row, Tag):
            continue
        if row.find_parent("table") is table:
            rows.append(row)
    return rows


def _row_cells(row: Tag) -> list[Tag]:
    return [
        cell
        for cell in row.find_all(("th", "td"), recursive=False)
        if isinstance(cell, Tag)
    ]


def _has_complex_spans(rows: list[Tag]) -> bool:
    for row in rows:
        for cell in _row_cells(row):
            for attribute in ("rowspan", "colspan"):
                raw = str(cell.get(attribute, "1")).strip()
                try:
                    span = int(raw)
                except ValueError:
                    return True
                if span != 1:
                    return True
    return False


def _cell_text(cell: Tag, limit: int) -> tuple[str, bool]:
    owner_table = cell.find_parent("table")
    parts: list[str] = []
    for value in cell.stripped_strings:
        parent = value.parent if isinstance(value.parent, Tag) else None
        nearest_table = parent.find_parent("table") if parent is not None else None
        if nearest_table is owner_table:
            parts.append(str(value))
    bounded, truncated = _bounded_text(" ".join(parts), limit)
    return bounded or "", truncated


def _bounded_text(value: object, limit: int) -> tuple[str | None, bool]:
    if value is None:
        return None, False
    clean = " ".join(str(value).split()).strip()
    if not clean:
        return None, False
    return clean[:limit], len(clean) > limit
