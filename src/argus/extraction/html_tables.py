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
    """Extract only clearly semantic, simple HTML data tables with bounded shape."""

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
        if _looks_like_layout(table):
            extraction.layout_skipped += 1
            continue
        if not _looks_like_data(table):
            extraction.layout_skipped += 1
            continue

        rows = _direct_table_rows(table)
        if not rows:
            extraction.empty_skipped += 1
            continue
        if _has_complex_spans(rows):
            extraction.complex_skipped += 1
            continue

        normalized_rows: list[list[str]] = []
        row_truncated = False
        for row in rows:
            cells = _row_cells(row)
            if not cells:
                continue
            if len(cells) > column_limit:
                row_truncated = True
            values = [_cell_text(cell, cell_limit) for cell in cells[:column_limit]]
            normalized_rows.append(values)
            if len(normalized_rows) >= row_limit:
                if len(rows) > len(normalized_rows):
                    row_truncated = True
                break
            if total_rows + len(normalized_rows) >= total_row_limit:
                row_truncated = True
                extraction.truncated = True
                break

        if not normalized_rows:
            extraction.empty_skipped += 1
            continue
        first_cells = _row_cells(rows[0])
        first_is_header = bool(first_cells) and all(cell.name == "th" for cell in first_cells)
        headers: list[str] = []
        data_rows = normalized_rows
        if first_is_header:
            headers = normalized_rows[0]
            data_rows = normalized_rows[1:]
        if not data_rows and not headers:
            extraction.empty_skipped += 1
            continue

        column_count = max(
            [len(headers), *(len(row) for row in data_rows)],
            default=0,
        )
        caption_tag = table.find("caption", recursive=False)
        caption = _cell_text(caption_tag, cell_limit) if isinstance(caption_tag, Tag) else None
        if not caption:
            aria = table.get("aria-label")
            caption = _bounded_text(aria, cell_limit)
        extraction.tables.append(
            HtmlTable(
                index=index,
                caption=caption,
                headers=headers,
                rows=data_rows,
                column_count=column_count,
                truncated=row_truncated,
            )
        )
        total_rows += len(data_rows)
        if total_rows >= total_row_limit:
            extraction.truncated = True
            break
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


def _cell_text(cell: Tag, limit: int) -> str:
    for nested in cell.find_all("table"):
        nested.decompose()
    return _bounded_text(cell.get_text(" ", strip=True), limit) or ""


def _bounded_text(value: object, limit: int) -> str | None:
    if value is None:
        return None
    clean = " ".join(str(value).split()).strip()
    return clean[:limit] if clean else None
