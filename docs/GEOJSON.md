# GeoJSON Point factual normalization

ARGUS parses GeoJSON through the existing bounded JSON document path and then adds a conservative geospatial normalization layer. The original JSON dataset remains the primary structured Evidence; Point features are additional factual Observations derived from that already bounded payload.

## Standards boundary

ARGUS follows RFC 7946:

- a Feature has a `geometry` member that is either a Geometry object or JSON `null`;
- a FeatureCollection contains a JSON array of Feature objects;
- a Feature `id`, when present, is a JSON string or number;
- Point positions use `[longitude, latitude]` order;
- an optional third number may represent height;
- GeoJSON uses WGS 84 / OGC CRS84 longitude/latitude coordinates.

ARGUS does not swap coordinate axes heuristically.

## Parsing and limits

There is no second GeoJSON JSON parser. `.geojson`, `application/geo+json`, explicit GeoJSON objects served as ordinary JSON, and bounded `.geojson.gz` all pass through the existing `BoundedStructuredDataExtractor` first.

The existing structured-data limits therefore apply before GeoJSON normalization:

- source bytes;
- JSON node count;
- JSON depth;
- maximum array length / records;
- maximum object properties / columns;
- maximum string length.

A JSON document rejected by that parser never reaches the GeoJSON feature normalizer.

## Normalized features

The current factual layer normalizes only GeoJSON `Point` Features.

For every valid Point Feature ARGUS creates:

- `source_kind=geojson_point`;
- `entity_type=geospatial_feature`;
- `Observation.geo` from the first two numeric coordinates;
- source-declared Feature properties in `data.properties`;
- source-declared Feature `id` as `entity_id` when it is a JSON string or number;
- otherwise a deterministic collection-local fallback based on source URL and feature index;
- separate canonical Feature Evidence linked to the same dataset Snapshot.

`name` or `title` property may be exposed as Observation title; `description` may be exposed as Observation text. These values remain copied source data, not generated interpretation.

## Position validation

ARGUS accepts Point positions with exactly two or three numeric finite values:

```text
[longitude, latitude]
[longitude, latitude, altitude]
```

The altitude value is retained in raw `data.coordinates` but is not interpreted because the current `Point` contract is two-dimensional.

The following positions are not normalized into `Observation.geo`:

- string coordinates such as `["37.6", "55.7"]`;
- booleans;
- NaN or infinity;
- longitude outside -180..180;
- latitude outside -90..90;
- fewer than two values;
- more than three values.

Over-dimensional positions remain present in the original dataset Evidence; ARGUS does not silently flatten them.

## Unsupported geometry types

LineString, MultiPoint, MultiLineString, Polygon, MultiPolygon and GeometryCollection are intentionally not converted into points. ARGUS also does not create centroids or representative points.

They remain available in the original structured dataset. `geojson_summary` records how many non-Point geometries, unlocated Features, invalid Features and invalid Points were skipped by the Point normalizer.

A future general geometry contract can add those geometries without changing the meaning of the current Point Observations.

## Compressed GeoJSON

`.geojson.gz` uses the same single-member bounded gzip path as other compressed structured documents. The dataset keeps the compressed-source SHA-256 identity and compression provenance; GeoJSON Point Observations reuse the dataset Snapshot and reference its Observation ID.

## Evidence and provenance

Each Point Observation records:

- source URL;
- parent dataset Observation ID;
- shared Snapshot ID;
- Feature index;
- extractor version `geojson-point/1`;
- axis order `longitude_latitude`;
- CRS marker `WGS84_CRS84`;
- `source_declared=true`.

No geocoding, reverse geocoding, geometry repair, CRS transformation or business interpretation is performed by this layer.
