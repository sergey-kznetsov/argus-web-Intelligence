# Публичные структурированные данные

ARGUS нормализует уже fetched публичные CSV, TSV, JSON и XML в evidence-backed Observation/Evidence. Этот path является частью Generic Web и не содержит consumer-specific logic.

## Boundary

Structured extraction — factual normalization. ARGUS может decode/parse/bound source-declared fields, но не рассчитывает score, demand, competition и другие предметные выводы.

Parser не выполняет network access. Remote JSON references, schemas и contexts не dereference'ятся. XML external entities/DTD expansion отклоняет `defusedxml`.

## Detection

Документ eligible, если:

- HTTP media type указывает CSV/TSV/JSON/XML;
- URL имеет `.csv`, `.tsv`, `.tab`, `.json`, `.geojson`, `.xml`;
- plain/octet-stream body начинается JSON object/array marker или XML declaration после BOM/whitespace.

Поддерживаются media suffixes `+json`, `+xml`. PDF обрабатывается отдельным path.

Recognized document response остаётся в FAST runtime, чтобы сохранить bounded raw response bytes. Текст вроде `enable javascript` внутри JSON/CSV/XML/PDF не должен запускать BROWSER. HTML shell может использовать normal FAST -> BROWSER.

## CSV/TSV

CSV delimiter detection ограничен:

```text
comma
semicolon
tab
pipe
```

TSV использует tab. Header detection — deterministic Python `csv.Sniffer`; `has_header` сохраняется в metadata.

Duplicate column names получают `_2`, `_3` и т.д.; empty header -> `column_N`.

С header:

```json
{
  "columns": ["name", "value"],
  "records": [{"name": "School", "value": "3"}]
}
```

Без header:

```json
{
  "columns": [],
  "rows": [["School", "3"]]
}
```

Cells остаются strings; numeric/date/currency/category type не угадывается.

### Encoding

Delimited data decode order:

```text
Unicode BOM
explicit HTTP charset
UTF-8 / UTF-8 BOM
Windows-1251 fallback
```

BOM имеет приоритет как часть source bytes. Invalid declared charset не прекращает extraction, если следующий supported deterministic encoding подходит.

Windows-1251 нужен для legacy Cyrillic CSV/TSV. Unrestricted Latin-1 fallback отсутствует, чтобы не превращать arbitrary binary в ложный текст.

## JSON

Используется Python standard library. Network JSON следует RFC 8259 и decode'ится UTF-8; HTTP charset не используется для legacy reinterpretation. UTF-8 BOM допускается.

NaN/Infinity отклоняются. Parsed JSON сохраняется только если вся structure помещается в configured depth/node/string/container limits.

JSON structural limits не приводят к silent truncation: вместо этого partial structured-file result получает `STRUCTURED_DATA_LIMIT_EXCEEDED`.

## XML

XML парсится `defusedxml`; external entities/XInclude/schemas/parser network отсутствуют.

Normalized payload сохраняет source tree с namespace-expanded tags/attributes, text, children и meaningful tail text. Types из XML text не выводятся.

Encoding precedence: BOM; затем authoritative HTTP charset; иначе XML declaration/parser rules. Если authoritative charset не decode'ит bytes, возвращается `STRUCTURED_DATA_DECODE_ERROR`, без guessing.

XML bounded по total nodes, depth, direct children, attributes и string size. Structural limit violation отклоняет normalized payload, а не создаёт тихо обрезанное tree.

## Identity и Evidence

SHA-256 исходных bounded response bytes — document content identity.

Successful structured document:

- `source_kind=structured_data`;
- `entity_type=dataset`;
- `binary_sha256` и byte length;
- parser metadata/goals;
- parsed payload;
- collection-scoped Snapshot;
- `structured_data` Evidence с bounded canonical JSON.

XML также сохраняет `node_count`, `max_depth`. Provenance: `parser_network_access=false`.

Если parsing fails, ARGUS всё равно сохраняет hash-backed `structured_file` Evidence факта получения public file; result становится partial с structured error.

## Limits

Defaults:

```text
ARGUS_STRUCTURED_DATA_MAX_BYTES=5242880
ARGUS_STRUCTURED_DATA_MAX_RECORDS=1000
ARGUS_STRUCTURED_DATA_MAX_COLUMNS=100
ARGUS_STRUCTURED_DATA_MAX_CELL_CHARS=10000
ARGUS_STRUCTURED_DATA_MAX_JSON_DEPTH=32
ARGUS_STRUCTURED_DATA_MAX_JSON_NODES=20000
```

XML переиспользует node/depth settings JSON. Transport response limit применяется раньше parser limits.

CSV/TSV clipping даёт explicit `STRUCTURED_DATA_TRUNCATED`. JSON/XML structural overflow отклоняет parsed payload вместо silent incomplete object.

## Errors

```text
STRUCTURED_DATA_BINARY_UNAVAILABLE
STRUCTURED_DATA_TOO_LARGE
STRUCTURED_DATA_DECODE_ERROR
STRUCTURED_DATA_PARSE_ERROR
STRUCTURED_DATA_LIMIT_EXCEEDED
STRUCTURED_DATA_UNSUPPORTED
STRUCTURED_DATA_TRUNCATED
```

Parser errors non-retryable для тех же source bytes; отсутствие retained response bytes может быть retryable через transport fetch.
