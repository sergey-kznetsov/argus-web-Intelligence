# Schema.org factual type normalization

ARGUS extracts source-declared structured entities from embedded JSON-LD and HTML Microdata. The raw structured payload remains evidence; schema.org normalization only adds a conservative factual category to `Observation.entity_type`.

## Boundary

ARGUS does not resolve remote vocabularies, load schema.org definitions at runtime, or infer business meaning from consumers such as Kraken or Janus.

Normalization uses only:

- explicit schema.org type URLs such as `https://schema.org/Review`;
- simple JSON-LD type tokens such as `Review` only when the source explicitly declares a schema.org `@context` / `@vocab` hint.

Unknown vocabularies remain `structured_entity`.

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

A recognized schema.org type with no mapping remains `structured_entity`. ARGUS does not fetch the live schema.org hierarchy to discover subclasses.

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

## Microdata

Microdata normalization uses explicit `itemtype` values. Because HTML Microdata item types are source-declared URLs, ARGUS recognizes only schema.org URLs and does not apply a hidden default vocabulary.

The original `item_types` and properties remain unchanged in Observation data.

## Stable identity

`entity_type` participates in deterministic Observation identity. When schema.org normalization changes an entity from `structured_entity` to a more specific factual type, ARGUS recomputes the Observation ID and the linked Evidence ID so persisted identity matches the normalized factual model.

## Non-goals

This layer does not:

- score or judge a review;
- decide whether a publication is important;
- infer organization categories for business analytics;
- expand arbitrary JSON-LD contexts;
- perform ontology reasoning;
- make consumer-specific conclusions.

Those responsibilities remain outside ARGUS factual collection.
