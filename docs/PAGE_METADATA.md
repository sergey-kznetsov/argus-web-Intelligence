# Source-declared HTML metadata

ARGUS extracts a bounded set of machine-readable metadata declared directly by public HTML pages. The layer complements visible page text and JSON-LD; it does not replace either one.

The initial vocabulary is intentionally limited to broadly deployed standards:

- Open Graph core properties;
- Open Graph `article:*` properties;
- Dublin Core / DCMI title, creator, date and description terms;
- HTML `rel=canonical`;
- ordinary HTML description/author metadata.

No date, author or canonical identity is guessed from body text, URL patterns or CSS classes.

## Extraction boundary

The extractor is local and network-free. It scans at most the configured internal hard limit of the beginning of the HTML document (500,000 characters by default), bounds individual metadata values, and bounds repeated arrays such as authors and tags.

Only HTTP(S) canonical/`og:url` syntax without URL userinfo is retained. These source-declared URLs are not fetched by the extractor. If a URL later becomes a crawl task it still passes the normal ARGUS SSRF/redirect guard.

Open Graph conflict handling follows source order: for singleton properties the first declared value wins. Repeated `article:author` and `article:tag` values are retained as bounded arrays.

## Date semantics

ARGUS distinguishes declaration from interpretation.

`article:published_time` has explicit publication semantics and may populate `Observation.published_at` when it is valid ISO-style datetime data.

Dublin Core `date` is broader: DCMI defines it as a point or period associated with an event in the resource lifecycle. ARGUS therefore stores `dc_date` / `dcterms_date` exactly as declared but does not relabel those values as publication time.

Likewise `dcterms.created` is retained as source-declared creation metadata rather than silently converted to publication time.

## Observation and Evidence

When at least one supported metadata field exists, Generic Web emits an additional Observation:

- `source_kind = page_metadata`;
- `entity_type = document_metadata`;
- URL remains the actually fetched final URL;
- entity identity may use a safely declared canonical URL;
- data contains the normalized source-declared fields;
- provenance links to the same raw page Snapshot as the visible page Observation;
- quality records `evidence_backed`, `machine_readable` and `source_declared`.

A separate `page_metadata` Evidence row contains canonical JSON of the extracted fields and references the actually fetched source URL. This prevents a canonical declaration from replacing the proof location.

Pages without supported metadata continue to emit only the ordinary Generic Web Observation/Evidence, preserving the previous simple path.

## Scope

This layer is useful for local media, official websites, public portals and other pages that expose stable machine-readable metadata while frequently changing their visible DOM.

It does not:

- infer article status from layout;
- treat all dates as publication dates;
- use metadata to rank domains yet;
- deduplicate URLs by canonical declaration yet;
- execute remote metadata contexts;
- make consumer-specific business conclusions.

Canonical ranking/deduplication belongs to the later discovery-quality stage. This extractor only makes the source declaration available as evidence-backed fact.
