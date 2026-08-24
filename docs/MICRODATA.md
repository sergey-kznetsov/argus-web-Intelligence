# HTML Microdata extraction

ARGUS can normalize explicitly declared HTML Microdata (`itemscope` / `itemprop`) as an additional evidence-backed factual layer on public pages.

The implementation follows the HTML Microdata value model conservatively and performs no vocabulary lookup or remote schema resolution.

```text
public HTML page
    -> page Snapshot
    -> bounded Microdata extractor
    -> Observation(source_kind=microdata)
    -> Evidence(type=microdata)
```

The ordinary `web_page` Observation remains unchanged. Microdata is additional factual structure, not a replacement for the fetched page.

## Supported source declarations

An element with `itemscope` creates an item. Descendant `itemprop` values belonging to that item are collected in source order. Repeated properties remain ordered lists.

Supported value rules include:

- nested `itemscope` element -> nested item reference;
- `<meta>` -> `content`;
- media elements -> `src`;
- `<a>`, `<area>`, `<link>` -> `href`;
- `<object>` -> `data`;
- `<data>`, `<meter>` -> `value`;
- `<time>` -> `datetime` when present;
- other elements -> bounded source text owned by the current item.

Nested items are also emitted as independent Microdata observations when they fall within configured limits. Their internal properties are not copied into the parent item.

## itemref policy

HTML Microdata allows `itemref` to reference additional elements elsewhere in the same document tree.

ARGUS Microdata v1 deliberately does not partially interpret `itemref`. An item containing `itemref` is skipped entirely and counted in `itemref_skipped`.

This avoids publishing a locally visible subset as if it represented the complete source-declared item. Support for `itemref` should only be added with bounded cycle detection, deterministic tree-order traversal and explicit tests for malformed reference graphs.

## URLs and identifiers

Relative HTTP/HTTPS values are resolved against the actually fetched page URL.

URL-valued properties reject unsupported or unsafe schemes instead of rewriting them. An omitted unsafe/unrepresentable value marks the affected item and extraction as incomplete (`truncated=true`).

`itemid` can be used as entity identity, but it never replaces the URL of the evidence. `Observation.url` and `Evidence.source.url` remain the actually fetched page URL.

## Bounds

No new environment settings are introduced. Runtime limits reuse the existing structured-data configuration:

- scan characters: `min(structured_data_max_bytes, 750_000)`;
- items: `min(structured_data_max_records, 100)`;
- properties per item: `min(structured_data_max_columns, 100)`;
- property value characters: `structured_data_max_cell_chars`;
- values per repeated property: hard upper bound of 20;
- item types: hard upper bound of 10;
- property names per `itemprop` attribute: hard upper bound of 20.

Any clipping, omitted overlong token, over-budget property/value, invalid URL value or scan/item limit is surfaced through `truncated`. Silent truncation is not allowed.

## Observation model

Each normalized item becomes:

```text
source       = generic_web
source_kind  = microdata
entity_type  = structured_entity
url          = actually fetched page URL
entity_id    = source itemid, otherwise a stable page-local identity
```

`data` contains:

```json
{
  "item_types": ["https://schema.org/NewsArticle"],
  "item_id": "https://example.org/article/1",
  "properties": {
    "headline": ["Example"],
    "datePublished": ["2026-08-20T10:00:00+04:00"]
  },
  "truncated": false
}
```

`title` is populated only from explicit `name` or `headline`. `text` is populated only from explicit `description` or `abstract`. `published_at` is populated only from a parseable explicit `datePublished` value.

ARGUS does not use item vocabulary knowledge to invent aliases or infer missing properties.

## Provenance and Evidence

Microdata provenance includes:

- parent page `snapshot_id`;
- actually fetched `page_url`;
- source item index;
- research goals;
- extractor version;
- item/extraction truncation state;
- count of skipped `itemref` items;
- `remote_vocabularies_resolved=false`.

Evidence contains a bounded canonical JSON excerpt and includes the canonical SHA-256 so an excerpt can be related to the complete normalized item held in the Observation and the original HTML Snapshot.

## Non-goals

Microdata v1 does not:

- fetch or interpret remote vocabularies;
- validate schema.org business semantics;
- execute `itemref` traversal;
- infer properties absent from markup;
- merge JSON-LD, Microformats2 and Microdata into one business entity;
- score truthfulness of source claims;
- make Kraken/Janus conclusions.

The layer preserves the ARGUS boundary: obtain and prove source-declared facts without adding consumer-specific interpretation.
