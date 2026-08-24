# Schema.org factual normalization

ARGUS extracts source-declared structured entities from embedded JSON-LD and HTML Microdata. The raw structured payload remains Evidence; schema.org normalization adds conservative factual categories and selected source-declared fields to the Observation envelope.

## Boundary

ARGUS does not resolve remote vocabularies, load schema.org definitions at runtime, or infer business meaning from consumers such as Kraken or Janus.

Normalization uses only:

- explicit schema.org type URLs such as `https://schema.org/Review`;
- simple JSON-LD type tokens such as `Review` only when the source explicitly declares a schema.org `@context` / `@vocab` hint.

Unknown vocabularies remain `structured_entity` and do not receive schema-specific field normalization.

## Supported factual categories

Current mappings are intentionally small and stable:

- Schema.org Review family -> `review`;
- Article / NewsArticle / posting/report family -> `publication`;
- Comment -> `comment`;
- Dataset / DataCatalog -> `dataset`;
- Event and `*Event` schema.org types -> `event`;
- Organization and `*Organization` schema.org types -> `organization`;
- Person -> `person`;
- Place -> `place`;
- Product -> `product`;
- Service -> `service`.

A recognized schema.org type with no mapping remains `structured_entity`. For example, `GeoCoordinates` is recognized as schema.org but remains a structured entity because the current ARGUS category set has no separate coordinate-entity class.

ARGUS does not fetch the live schema.org hierarchy to discover subclasses.

## JSON-LD context handling

Embedded JSON-LD remains network-free. `EmbeddedJsonLdExtractor` retains only bounded context hints:

- a string `@context`;
- string entries of a context array;
- explicit `@vocab` strings from bounded context objects.

For `@graph`, child entities inherit the root context hint unless they declare their own context. No context URL is dereferenced.

The original JSON-LD data remains unchanged in Observation data. Provenance records:

- recognized schema.org local types;
- normalized ARGUS entity type;
- bounded context hints;
- `remote_vocabularies_resolved=false`.

## Source-declared text and publication date

For recognized schema.org entities ARGUS may expose a bounded source-declared text field through `Observation.text`:

- Review: `reviewBody`, then `description`;
- publication: `articleBody`, then `text`, then `description`;
- Comment: `text`, then `description`;
- other mapped factual categories: `description`.

`datePublished` is parsed as `Observation.published_at` only when it is a valid ISO-style date/datetime string. ARGUS does not infer dates from prose or substitute event start times for publication times.

Provenance and Evidence metadata record exactly which source field was used.

## Source-declared coordinates

Schema.org defines `latitude` and `longitude` for `GeoCoordinates` and `Place`, and commonly embeds `GeoCoordinates` through the `geo` property.

ARGUS supports:

- JSON-LD `Place.geo.latitude/longitude`;
- JSON-LD direct `latitude/longitude` on recognized schema.org entities;
- Microdata `GeoCoordinates` or recognized schema.org entities with explicit `latitude/longitude` properties.

Values may be numeric or numeric text. Both coordinates must be finite and inside WGS84 ranges:

- latitude: -90..90;
- longitude: -180..180.

ARGUS never swaps coordinates, geocodes an address, or repairs malformed values. Invalid declared coordinates remain visible in raw Evidence and are marked `geospatial_valid=false`; `Observation.geo` remains empty.

Provenance records `geocoding_used=false`.

## Microdata

Microdata normalization uses explicit `itemtype` values. Because HTML Microdata item types are source-declared URLs, ARGUS recognizes only schema.org URLs and does not apply a hidden default vocabulary.

The original `item_types` and properties remain unchanged in Observation data.

## Stable identity

`entity_type` participates in deterministic Observation identity. When schema.org normalization changes an entity from `structured_entity` to a more specific factual type, ARGUS recomputes the Observation ID and the linked Evidence ID so persisted identity matches the normalized factual model.

Changing display text, publication date or source-declared coordinates does not create a second identity because the content hash already represents the canonical raw structured payload from which those fields were derived.

## Non-goals

This layer does not:

- score or judge a review;
- decide whether a publication is important;
- infer organization categories for business analytics;
- expand arbitrary JSON-LD contexts;
- perform ontology reasoning;
- geocode missing coordinates;
- make consumer-specific conclusions.

Those responsibilities remain outside ARGUS factual collection.
