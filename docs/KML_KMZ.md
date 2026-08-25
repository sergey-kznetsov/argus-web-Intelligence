# KML and KMZ factual geospatial extraction

ARGUS supports public KML Point facts and KMZ packages without introducing a second XML parser or a general-purpose archive extraction surface.

## Pipeline

Plain KML:

```text
HTTP response
  -> bounded structured-data XML parser
  -> source XML dataset Observation/Evidence
  -> KML Placemark/Point normalization
  -> geospatial_feature Observation + kml_point Evidence
```

Gzip-wrapped KML:

```text
.kml.gz
  -> bounded single-member gzip
  -> bounded XML parser
  -> shared KML normalizer
```

KMZ:

```text
.kmz
  -> bounded ZIP preflight
  -> root doc.kml only
  -> bounded XML parser
  -> shared KML normalizer
```

The fetched source URL remains the factual source URL. A KMZ package keeps the SHA-256 of the package as the source-document identity while provenance separately records the SHA-256 of `doc.kml`.

## KML support

The current factual KML subset is intentionally conservative:

- `Placemark`;
- direct `Point` geometry;
- `name`;
- `description`;
- `coordinates` containing one two- or three-dimensional tuple.

KML coordinates are interpreted in source order:

```text
longitude,latitude[,altitude]
```

Longitude must be within `[-180, 180]`, latitude within `[-90, 90]`, and every supplied numeric value must be finite. ARGUS does not swap axes, repair coordinates, infer a location from text, or geocode an invalid KML Point.

Altitude, when present, is retained as source-declared data but is not interpreted by ARGUS.

Each accepted Point produces:

- `source_kind=kml_point`;
- `entity_type=geospatial_feature`;
- normalized `Observation.geo` latitude/longitude;
- source-declared coordinate tuple in Observation data;
- canonical bounded Placemark Evidence;
- the parent XML/KMZ Snapshot ID and dataset Observation ID in provenance.

## Unsupported geometry

`LineString`, `LinearRing`, `Polygon`, `MultiGeometry` and `Model` are not converted to Points. ARGUS does not calculate centroids or representative points because doing so would create derived geography rather than preserve a source-declared fact.

Unsupported geometry remains available inside the bounded source XML dataset. The KML summary records how many such Placemarks were skipped.

## NetworkLink boundary

ARGUS never follows KML `NetworkLink` during KML normalization.

The bounded XML dataset may contain the source-declared `NetworkLink` data as Evidence, but KML factual extraction performs zero additional network requests. The summary and KMZ metadata explicitly record `network_links_followed=0/false`.

Any later retrieval of a public destination must go through the normal ARGUS discovery/source/URL-security path; a KML document cannot bypass SSRF or crawler policy by declaring a link internally.

## KML limits

KML reuses the existing structured-data safety surface:

- source bytes are transport/parser bounded;
- XML uses `defusedxml` and the configured node/depth/string/container limits;
- `kml_max_placemarks` is bound to `ARGUS_STRUCTURED_DATA_MAX_RECORDS` in production bootstrap.

If more Placemarks are present than the normalization budget, ARGUS keeps the bounded facts, marks the dataset and result partial, and emits `KML_EXTRACTION_TRUNCATED`.

## KMZ package security

KMZ is treated as an untrusted ZIP package. The package is never extracted to disk.

Preflight rejects:

- packages over the compressed byte limit;
- too many file members;
- total declared uncompressed size over the configured limit;
- individual members over the member limit;
- encrypted members;
- symbolic-link members;
- unsupported ZIP compression methods;
- absolute, traversal, backslash, NUL or drive-like member paths;
- duplicate and case-colliding member names;
- packages without root `doc.kml`.

Only root `doc.kml` is read. Images, overlays and other KMZ resources participate in package-size/path validation but are not resolved or rendered.

The production defaults are derived from the existing structured-data budget rather than adding another operator configuration surface:

- compressed KMZ bytes = `ARGUS_STRUCTURED_DATA_MAX_BYTES`;
- member count = bounded by `ARGUS_STRUCTURED_DATA_MAX_RECORDS`, capped at 1000;
- total uncompressed bytes = up to 4x structured byte budget, capped at 20 MiB;
- per-member bytes = up to 2x structured byte budget, capped at 10 MiB;
- `doc.kml` bytes = structured byte budget.

## KMZ provenance

KMZ dataset and Point facts record:

- package SHA-256 and byte length;
- member count;
- declared total uncompressed bytes;
- `root_kml=doc.kml`;
- `doc.kml` SHA-256 and byte length;
- KMZ extractor version;
- `resources_resolved=false`;
- `network_links_followed=false`.

This keeps the package, the parsed KML and each normalized Point auditable without treating unused archive resources as factual observations.

## Non-goals

This layer does not:

- render maps;
- download KMZ resources;
- execute KML tours or overlays;
- follow NetworkLink;
- calculate Polygon/LineString centroids;
- infer missing coordinates;
- make Kraken-, Janus- or other consumer-specific conclusions.
