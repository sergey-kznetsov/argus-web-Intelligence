# RSS and Atom factual feed extraction

ARGUS treats RSS 2.x and Atom feeds as factual publication sources. Feed discovery is navigation only; an entry becomes an Observation only after ARGUS fetches and safely parses the feed document itself.

## Security boundary

Feed XML is untrusted input.

Before ARGUS builds the semantic XML tree, it performs a streaming `defusedxml.iterparse` preflight with explicit node and depth budgets. Parsed elements are cleared during preflight. Only a feed that passes this preflight is parsed again for RSS/Atom semantics.

This provides two independent properties:

- entity/DTD attacks are rejected by `defusedxml`;
- deeply or broadly nested XML is rejected before ARGUS constructs the full semantic tree.

Failures are normalized as:

- `FEED_XML_INVALID` for malformed or unsafe XML;
- `FEED_XML_LIMIT_EXCEEDED` when node/depth budgets are exceeded.

No XML external entities, schemas, XInclude or remote resources are resolved.

## Runtime limits

RSS/Atom reuses the existing structured-data budget instead of defining a second configuration surface.

At bootstrap:

- `max_items = min(ARGUS_STRUCTURED_DATA_MAX_RECORDS, 100)`;
- XML node budget = `ARGUS_STRUCTURED_DATA_MAX_JSON_NODES`;
- XML depth budget = `ARGUS_STRUCTURED_DATA_MAX_JSON_DEPTH`;
- title and identifier limits derive from `ARGUS_STRUCTURED_DATA_MAX_CELL_CHARS`;
- entry body text is derived from the same cell limit and capped at 100,000 characters.

If an item or field limit is reached, ARGUS returns the bounded entries with `partial=true` and `FEED_EXTRACTION_TRUNCATED`. Truncation is also recorded on the affected Observation and Evidence metadata.

## Atom links

For Atom entries ARGUS prefers an entry link whose `rel` is `alternate` or omitted. RFC 4287 defines an omitted `rel` as `alternate`. A non-alternate link such as `self` is only a fallback when no alternate link is present.

Only HTTP/HTTPS entry URLs without embedded credentials are accepted. Unsafe or invalid entry URLs fall back to the fetched feed URL instead of being used as factual destinations.

## Source-declared geography

ARGUS supports two point forms when the feed entry itself declares them:

- GeoRSS Simple `georss:point`;
- GeoRSS GML `georss:where/gml:Point/gml:pos`.

Coordinates are interpreted in the GeoRSS order `latitude longitude` and are accepted only when both values are finite and inside WGS84 latitude/longitude ranges. ARGUS does not geocode or guess a replacement when a declared point is malformed.

A valid point is copied to `Observation.geo`. Provenance records the GeoRSS representation and `geocoding_used=false`. An invalid but explicitly declared point remains visible through `data.geospatial` and `quality.geospatial_valid=false` while `Observation.geo` stays empty.

Only points are normalized in the current contract. GeoRSS lines, boxes and polygons are deliberately not flattened into a point because the current Observation contract exposes a `Point`, not a general geometry.

## Evidence and provenance

Each bounded feed entry produces:

- one `Observation` with `source_kind=feed_entry` and `entity_type=publication`;
- one `Evidence` item sourced from the fetched feed URL;
- the shared feed Snapshot ID;
- feed format (`rss` or `atom`);
- entry index and total feed-entry count;
- XML node/depth statistics;
- explicit truncation flags;
- source-declared GeoRSS point metadata when present.

The feed Snapshot is captured from the fetched source document before semantic normalization, preserving the evidence needed to audit the extracted entry later.

## Deliberate limitations

ARGUS does not execute embedded HTML/JavaScript from feed fields. It does not dereference feed entry URLs during feed normalization; those are separate web retrieval tasks when the research plan requires them. It also does not infer publication dates from prose: only source-declared RSS/Atom date elements are parsed.
