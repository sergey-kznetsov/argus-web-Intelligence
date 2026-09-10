# Извлечение семантических HTML-таблиц

ARGUS может превращать простые публичные HTML data tables в evidence-backed `Observation` без site-specific parser.

```text
public page
  -> fetched HTML
  -> page Snapshot
  -> bounded semantic table extractor
  -> Observation(source_kind=html_table)
  -> Evidence(type=html_table)
```

Table layer не заменяет обычный `web_page` Observation, а добавляет структурированное представление рядом с ним.

## Что считается data table

Таблица рассматривается как данные только при явной semantic markup, например:

- direct `<caption>`;
- `<thead>`;
- `<th>`;
- `role="table"`, `role="grid"`, `role="treegrid"`;
- `aria-label`/`aria-labelledby`.

`role="presentation"` и `role="none"` означают layout и пропускаются.

Обычный `<table><td>...</td></table>` без semantic indicators не превращается в factual dataset.

## Complex spans

Версия 1 нормализует только rectangular tables, где effective `rowspan=1` и `colspan=1` для всех cells.

Merged grid, invalid span или другое значение помечаются как `complex_skipped`. ARGUS не дублирует и не угадывает merged-cell values; исходный HTML остаётся в page Observation/Snapshot.

## Nested tables

Rows принадлежат ближайшей owning table. Text nested table не копируется в parent cell. Вложенная table может извлекаться отдельно, если у неё есть собственная data-table semantics.

## Bounds

Используется существующий structured-data budget:

```text
scan chars = min(structured_data_max_bytes, 1000000)
rows/table = min(structured_data_max_records, 200)
total rows = structured_data_max_records
columns = structured_data_max_columns
cell/caption chars = structured_data_max_cell_chars
tables scanned <= 20
```

Clipping по scan/table/row/column/cell/caption отражается как `truncated=true`.

## Observation

```text
source      = generic_web
source_kind = html_table
entity_type = dataset
url         = реально fetched page URL
title       = source caption/aria-label при наличии
```

`data` содержит `caption`, `headers`, `rows`, `column_count`, `truncated`. Stable factual hash вычисляется из canonical JSON normalized table.

## Provenance

Сохраняются parent `snapshot_id`, fetched page URL, table index, extractor version, goals, truncation state, число skipped layout/complex tables. `quality.lossless=true` только если таблица не была clipped.

## Evidence

Отдельный `Evidence(type=html_table)` связан с реально fetched page URL. Evidence text — bounded до 10 000 символов excerpt canonical normalized JSON. Полная таблица остаётся в Observation, исходная страница — Snapshot.

Metadata содержит `canonical_sha256`, `evidence_excerpt_truncated`, `snapshot_id`, `table_index`, extractor version.

## Не входит в текущую реализацию

Extractor не:

- выводит таблицу из CSS layout;
- специально исполняет JavaScript для реконструкции table;
- угадывает multi-row/merged headers;
- разворачивает rowspan/colspan;
- интерпретирует numbers/currencies/dates;
- считает aggregates;
- формирует выводы consumer module.
