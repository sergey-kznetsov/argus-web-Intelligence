# Sitemap discovery

ARGUS uses `site_discovery` only as an internal navigation aid. Robots.txt and Sitemap entries never become factual coverage by themselves. A selected destination must still pass through `generic_web` and produce normal Observation/Evidence before a consumer can use it as a fact.

## Scope

Sitemap navigation is restricted to HTTP(S) URLs on the original hostname. Request-level allowed/denied domain constraints remain active for final page URLs. Sitemap-index fan-out, final URL fan-out, collection page budget and index depth remain bounded.

Missing, malformed, blocked or oversized Sitemap content is fail-open: ARGUS abandons that optional navigation branch without turning the collection into a factual source failure.

## Gzip Sitemap files

ARGUS accepts same-host `.xml.gz`/`.gz` Sitemap URLs declared in `robots.txt` or a Sitemap index. The FAST runtime retains the bounded response bytes; `site_discovery` detects actual gzip content from the gzip magic bytes or gzip media type and decompresses it locally.

Decompression uses a bounded streaming zlib operation. The maximum uncompressed Sitemap size is `ARGUS_MAX_RESPONSE_BYTES`, the same limit used for the fetched response body. A compressed document that would expand beyond that limit is rejected before XML parsing. Invalid, truncated or multi-member/trailing-data gzip payloads are ignored.

The decompressed XML is passed to `defusedxml`, exactly like an ordinary Sitemap. DTD/entity expansion remains disabled. Gzip support does not alter the evidence boundary: a Sitemap is still navigation metadata, not evidence.

## Recursion

A Sitemap index can enqueue only one further index level. Sitemap-discovered final pages are marked with `disable_site_discovery=true`, so those pages do not recursively launch another robots/Sitemap traversal.

This keeps discovery bounded even on sites with large or cyclic Sitemap graphs.
