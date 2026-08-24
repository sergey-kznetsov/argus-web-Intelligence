# Semantic HTML table extraction

ARGUS can normalize simple public HTML data tables into evidence-backed `Observation` records without introducing site-specific parsers.

The implementation follows the same boundary as the rest of ARGUS:

```text
public page
    -> fetched HTML
    -> page Snapshot
    -> bounded semantic table extractor
    -> Observation(source_kind=html_table)
    -> Evidence(type=html_table)
```

The table layer does not replace the ordinary `web_page` Observation. It adds a structured factual representation alongside the source page and points to the same page Snapshot.

## What counts as a data table

ARGUS only considers a table when the source page exposes explicit data-table semantics, for example:

- a direct `<caption>`;
- `<thead>`;
- `<th>` cells;
- `role="table"`, `role="grid"` or `role="treegrid"`;
- `aria-label` or `aria-labelledby`.

Tables with `role="presentation"` or `role="none"` are treated as layout and skipped.

This is intentionally conservative. A plain `<table><td>...</td></table>` without semantic indicators is not converted into a factual dataset.

## Complex spans

Version 1 only normalizes a rectangular table where every cell has effective:

```text
rowspan = 1
colspan = 1
```

Any table containing another span value, an invalid span value, or a merged grid is reported as `complex_skipped` and is not normalized.

ARGUS deliberately does not duplicate or guess merged-cell values. The original HTML page remains available through the ordinary page Observation and Snapshot.

## Nested tables

Rows belong only to their nearest owning table. Text from a nested table is not copied into the parent cell value.

A nested table can still be extracted independently when it has its own semantic data-table markers.

## Bounds

No additional environment configuration is introduced. Semantic table extraction reuses the structured-data budget configured for ARGUS.

Runtime wiring uses:

- scan characters: `min(structured_data_max_bytes, 1_000_000)`;
- rows per table: `min(structured_data_max_records, 200)`;
- total extracted rows: `structured_data_max_records`;
- columns: `structured_data_max_columns`;
- cell/caption characters: `structured_data_max_cell_chars`;
- tables scanned: hard upper bound of 20.

Any clipped scan, table count, row count, column count, cell or caption sets `truncated=true`. Silent truncation is not allowed.

## Observation model

Each normalized table becomes:

```text
source       = generic_web
source_kind  = html_table
entity_type  = dataset
url          = the actually fetched page URL
title        = source caption/aria-label when present
```

`data` contains:

```json
{
  "caption": "...",
  "headers": ["..."],
  "rows": [["..."]],
  "column_count": 2,
  "truncated": false
}
```

The stable factual hash is calculated from canonical JSON of this normalized table.

## Provenance

Table provenance includes:

- the parent page `snapshot_id`;
- the actually fetched `page_url`;
- table index within the scanned page;
- extractor version;
- research goals;
- extraction/table truncation state;
- counts of layout and complex tables skipped.

`quality.lossless` is `true` only when that table was not clipped by a configured bound.

## Evidence

Each table has separate `Evidence(type=html_table)` tied to the actually fetched page URL.

Evidence text is an excerpt of canonical normalized JSON, bounded to 10,000 characters. The complete table remains in the Observation and the source HTML remains in the Snapshot. Evidence metadata therefore includes:

- `canonical_sha256`;
- `evidence_excerpt_truncated`;
- `snapshot_id`;
- `table_index`;
- extractor version.

## Non-goals

The current extractor does not:

- infer a table from CSS layout;
- execute JavaScript specifically to reconstruct a table;
- guess multi-row/merged headers;
- expand `rowspan`/`colspan` grids;
- interpret numbers, currencies or dates;
- calculate aggregates;
- make Kraken/Janus business conclusions.

Those rules preserve the ARGUS boundary: find, obtain, prove and normalize factual source data without silently adding interpretation.
