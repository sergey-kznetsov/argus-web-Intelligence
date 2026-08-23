# Structured public data

ARGUS normalizes already-fetched public CSV, TSV and JSON documents into evidence-backed `Observation` and `Evidence` records. This path is part of Generic Web and does not introduce a consumer-specific branch.

## Boundary

Structured extraction is factual normalization only. ARGUS may decode, parse and bound source-declared fields, but it does not score, classify, infer demand, estimate competition or otherwise interpret the meaning of the dataset. Those conclusions remain the responsibility of Kraken, Janus or another consumer.

The parser performs no network access. Remote JSON references, schemas and contexts are not dereferenced.

## Detection

A document is eligible when its HTTP media type identifies CSV, TSV or JSON, when the URL has a known `.csv`, `.tsv`, `.tab`, `.json` or `.geojson` suffix, or when a plain/octet-stream body begins with a JSON object/array marker after optional UTF-8 BOM and whitespace.

JSON media types ending in `+json` are accepted. PDF detection remains a separate document path.

Recognized document responses remain in the FAST runtime so their bounded raw response body is preserved. Text fragments such as `enable javascript` inside JSON/CSV/PDF content must not cause Playwright escalation. HTML shells still use the normal FAST -> BROWSER -> AGENT escalation path.

## CSV and TSV

CSV delimiter detection is bounded to comma, semicolon, tab and pipe. TSV uses tab directly. Header detection uses Python's deterministic `csv.Sniffer` heuristic; `has_header` is included in normalized metadata so consumers can see that shape decision.

When a header is detected, duplicate column names are made unique deterministically by adding `_2`, `_3`, and so on. Empty header cells become `column_N`.

Normalized payloads use either:

```json
{
  "columns": ["name", "value"],
  "records": [
    {"name": "School", "value": "3"}
  ]
}
```

or, when no header is detected:

```json
{
  "columns": [],
  "rows": [["School", "3"]]
}
```

Cells remain strings. ARGUS does not guess numeric, date, currency or category types.

### Text encodings

Delimited public data is decoded deterministically rather than through statistical charset detection.

Order:

```text
Unicode BOM
explicit HTTP charset
UTF-8 / UTF-8 BOM
Windows-1251 fallback
```

BOM takes precedence because it is part of the source bytes. An invalid or unknown declared charset does not terminate extraction if a later supported deterministic encoding succeeds.

The Windows-1251 fallback exists for legacy Cyrillic CSV/TSV public datasets. ARGUS does not add unrestricted single-byte fallbacks such as Latin-1 because they decode arbitrary binary data and can silently produce false text.

## JSON

JSON is parsed with the Python standard library. Network JSON follows RFC 8259 and is decoded as UTF-8; an HTTP `charset` parameter is not used to reinterpret the body as a legacy encoding. UTF-8 BOM is tolerated for interoperability.

Non-standard numeric constants such as `NaN` and `Infinity` are rejected. Parsed JSON is preserved as source data only when the complete structure fits configured depth, node, string and container limits.

ARGUS does not silently truncate JSON structures because replacing omitted branches with invented sentinel values could be mistaken for source facts. A JSON document that exceeds structural limits becomes a partial structured-file result with an explicit `STRUCTURED_DATA_LIMIT_EXCEEDED` error.

## Identity and evidence

The SHA-256 of the original bounded response bytes is the document content identity. A normalized structured document produces:

- `source_kind=structured_data`;
- `entity_type=dataset`;
- `binary_sha256` and original byte length;
- parser metadata and research goals;
- parsed payload when extraction succeeds;
- a collection-scoped snapshot containing parser metadata plus canonicalized payload;
- `structured_data` Evidence containing a bounded canonical JSON representation.

If parsing fails, ARGUS still preserves evidence that the public file was retrieved by emitting hash-backed `structured_file` Evidence. The result is marked partial and carries a structured error code.

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

The transport response limit is applied before parser-specific limits. It may be configured more strictly than the structured-data byte limit.

For CSV/TSV, record, column or cell clipping is explicit: the normalized result is marked partial with `STRUCTURED_DATA_TRUNCATED`. For JSON, structural limit violations reject the parsed payload rather than returning a silently incomplete object.

## Errors

Current structured-data errors include:

```text
STRUCTURED_DATA_BINARY_UNAVAILABLE
STRUCTURED_DATA_TOO_LARGE
STRUCTURED_DATA_DECODE_ERROR
STRUCTURED_DATA_PARSE_ERROR
STRUCTURED_DATA_LIMIT_EXCEEDED
STRUCTURED_DATA_UNSUPPORTED
STRUCTURED_DATA_TRUNCATED
```

Parser errors are non-retryable because repeating the same bounded source bytes cannot repair malformed or oversized content. Missing retained response bytes are retryable because a new transport attempt may recover the source body.
