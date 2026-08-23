# JSON Feed source

ARGUS supports JSON Feed 1.0 and 1.1 as a factual public syndication source alongside RSS and Atom.

JSON Feed is treated as source data, not as a search result or analytical conclusion. Feed items become evidence-backed publication Observations. Kraken, Janus and other consumers remain responsible for interpretation.

## Discovery

Generic Web pages may advertise a JSON Feed through the standard HTML alternate link:

```html
<link rel="alternate" type="application/feed+json" href="/feed.json">
```

ARGUS resolves the URL against the page URL, removes fragments and applies the same allowed/denied-domain boundary used for RSS/Atom discovery.

`application/json` is intentionally not accepted as an HTML feed-discovery MIME type. It is too broad and commonly identifies arbitrary JSON APIs. Standard autodiscovery requires `application/feed+json`.

Explicit seed URLs use a deliberately narrow filename heuristic (`feed.json`, `*.feed.json`, `*.jsonfeed`) so a generic public JSON dataset is not duplicated as both a dataset and a publication feed.

## Parsing and limits

JSON Feed does not introduce a second JSON parser. The adapter uses ARGUS `BoundedStructuredDataExtractor`, so the existing structured-data controls apply before semantic feed handling:

- transport/parser byte limit;
- strict UTF-8 JSON decoding;
- rejection of NaN/Infinity;
- JSON node/depth/container/string limits;
- no parser network access.

After bounded JSON parsing the adapter requires:

- root object;
- `version` equal to `https://jsonfeed.org/version/1` or `https://jsonfeed.org/version/1.1`;
- non-empty string `title`;
- array `items`.

At most 100 items are normalized per feed, further bounded by `ARGUS_STRUCTURED_DATA_MAX_RECORDS`. Hitting the item budget is explicit partial coverage (`JSON_FEED_ITEM_LIMIT`), never silent truncation.

## Item normalization

A usable item requires:

- `id` as a string or JSON number (numbers are deterministically converted to strings);
- at least one non-empty `content_text` or `content_html` value.

Invalid items are skipped and reported as `JSON_FEED_ITEM_INVALID`, making the source result partial rather than pretending complete coverage.

`content_text` is preferred for the Observation text. If only `content_html` is present, ARGUS derives inert plain text with BeautifulSoup and removes script/style/noscript/svg nodes. The source HTML is not executed.

Item `url` is resolved against the feed URL and accepted only as public-looking HTTP(S) syntax without URL userinfo. Network SSRF validation still occurs when ARGUS later fetches any URL as a task; item URLs stored as provenance are not automatically fetched by this adapter.

## Identity, Evidence and provenance

Each valid item produces:

- `Observation.entity_type = publication`;
- `Observation.source_kind = json_feed_item`;
- stable Observation identity from collection, source, item id, item URL and canonical item content hash;
- canonical source item JSON in `Observation.data.item`;
- canonical source item JSON (bounded to the normal Evidence text limit) as `json_feed_item` Evidence;
- feed Snapshot identity;
- feed URL, item URL, JSON Feed version, research goals and structured-extractor version in provenance.

The item content hash is calculated from canonical source JSON rather than rendered plain text. Two source items that render similarly but differ in declared structured fields therefore remain distinguishable.

## Scope

The adapter does not:

- execute embedded HTML or scripts;
- follow `next_url` pagination automatically;
- interpret authors/tags as audience or business conclusions;
- treat arbitrary `application/json` links as feeds;
- bypass authentication, CAPTCHA or access controls.

Pagination and additional feed semantics can be added later if real source coverage demonstrates the need; they are deliberately omitted from the first implementation under YAGNI.
