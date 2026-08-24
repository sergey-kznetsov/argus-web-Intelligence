# Bounded gzip structured documents

ARGUS supports explicitly compressed public structured-data artifacts without using an unbounded decompression API.

Supported paths are:

- `.csv.gz`
- `.tsv.gz`
- `.tab.gz`
- `.json.gz`
- `.geojson.gz`
- `.xml.gz`

The gzip layer is a transport/container normalization step. The decompressed payload is still parsed by the existing bounded CSV/TSV/JSON/XML extractor.

```text
public *.csv.gz / *.json.gz / *.xml.gz
    -> bounded FAST body
    -> single-member streaming gzip decompression
    -> existing bounded structured-data parser
    -> Observation + Evidence + Snapshot
```

## Distinction from HTTP Content-Encoding

This feature handles an explicitly published gzip file. It does not treat ordinary HTTP `Content-Encoding: gzip` as a document format. HTTP transport decoding remains the responsibility of the HTTP client/runtime.

A URL must have a known structured inner suffix before `.gz`. The adapter also requires either gzip magic bytes or a gzip/octet-stream media type. A `text/html` error page returned for a `.csv.gz` URL is therefore not misclassified as a compressed dataset.

## Decompression boundary

ARGUS uses `zlib.decompressobj(16 + MAX_WBITS)` and limits every decompression call by the remaining allowed output plus one byte.

It does not use unbounded `gzip.decompress()` or `zlib.decompress()` for public artifacts.

The compressed input and uncompressed output are bounded independently. By default both limits reuse `structured_data_max_bytes`, so a tiny compressed payload cannot expand into an arbitrarily large parser input.

## Single-member policy

Version 1 accepts exactly one gzip member.

Concatenated members and any trailing bytes are rejected with `GZIP_TRAILING_DATA`. They are not silently concatenated into one dataset. This keeps one URL mapped to one deterministic structured payload and avoids hidden multi-dataset expansion.

## Errors

The gzip layer can return:

- `GZIP_COMPRESSED_TOO_LARGE`
- `GZIP_UNCOMPRESSED_LIMIT_EXCEEDED`
- `GZIP_INVALID`
- `GZIP_TRUNCATED`
- `GZIP_TRAILING_DATA`
- `GZIP_BINARY_UNAVAILABLE`

A retrieved gzip artifact that cannot be decompressed remains evidence-backed. ARGUS returns a partial structured-file result containing the hash and byte size of the compressed source plus the structured error. It does not retry the same file through Playwright.

## Source identity and provenance

The original compressed bytes remain the source identity.

`Observation.content_hash`, `binary_sha256` and the stable document observation ID are based on the compressed public artifact, not on the decompressed payload.

Compression metadata additionally records:

- compression format (`gzip`);
- `single_member_required=true`;
- compressed byte count;
- uncompressed byte count;
- compressed SHA-256;
- uncompressed SHA-256 when decompression succeeded;
- logical inner URL with `.gz` removed;
- gzip extractor version;
- gzip error code, when present.

This metadata is copied into Observation data/provenance and Evidence metadata.

## Inner parsing

After successful decompression ARGUS passes the bytes to the existing bounded structured-data extractor with the logical inner filename. The gzip media type is not reused as the inner content type.

As a result:

- JSON still requires UTF-8 according to the current JSON policy;
- XML keeps the hardened `defusedxml` path and XML bounds;
- CSV/TSV retain their deterministic encoding policy and record/column/cell limits.

No parser gains network access through gzip support.

## Runtime limits

No new environment variables are introduced. The gzip extractor derives both compressed and uncompressed byte caps from `structured_data_max_bytes`.

This is deliberate KISS/YAGNI. A separate compressed-data configuration surface should only be introduced if production measurements demonstrate a need for a different ratio.

## Non-goals

Version 1 does not:

- recursively unpack `.gz.gz`;
- parse `.zip`, `.7z`, `.rar` or tar archives through this path;
- accept concatenated gzip members;
- infer the inner format from arbitrary compressed bytes;
- make consumer-specific Kraken/Janus decisions.
