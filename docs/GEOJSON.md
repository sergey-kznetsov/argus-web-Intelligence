# Нормализация GeoJSON Point

ARGUS разбирает GeoJSON через существующий bounded JSON path и затем добавляет консервативную geospatial normalization. Исходный JSON dataset остаётся primary structured Evidence, а Point features создаются как дополнительные factual Observations.

## Стандарт

ARGUS следует RFC 7946:

- Feature содержит `geometry` либо JSON `null`;
- FeatureCollection содержит array Feature;
- Feature `id`, если есть, должен быть JSON string или number;
- Point position использует `[longitude, latitude]`;
- необязательное третье число может быть altitude;
- CRS — WGS 84 / OGC CRS84 longitude/latitude.

ARGUS не меняет оси эвристически.

## Parsing и limits

Отдельного GeoJSON parser нет. `.geojson`, `application/geo+json`, явные GeoJSON objects в JSON и bounded `.geojson.gz` сначала проходят `BoundedStructuredDataExtractor`.

До geospatial normalization применяются обычные limits:

- source bytes;
- JSON node count;
- depth;
- max array length/records;
- max object properties/columns;
- max string length.

JSON, отклонённый bounded parser, не попадает в GeoJSON normalizer.

## Нормализованные features

Текущий factual layer нормализует только `Point` Features.

Для каждого валидного Point создаются:

- `source_kind=geojson_point`;
- `entity_type=geospatial_feature`;
- `Observation.geo` из первых двух coordinates;
- source-declared properties в `data.properties`;
- source-declared Feature `id` как `entity_id`, если это string/number;
- иначе deterministic collection-local identity из source URL + feature index;
- отдельный canonical Feature Evidence, связанный с тем же dataset Snapshot.

`name`/`title` может стать Observation title, `description` — text. Это source data, не generated interpretation.

## Position validation

Принимаются ровно 2 или 3 finite numeric values:

```text
[longitude, latitude]
[longitude, latitude, altitude]
```

Altitude сохраняется в raw `data.coordinates`, но текущий `Point` contract остаётся двумерным.

Не нормализуются в `Observation.geo`:

- string coordinates;
- booleans;
- NaN/Infinity;
- longitude вне -180..180;
- latitude вне -90..90;
- меньше двух или больше трёх values.

Исходный dataset Evidence при этом сохраняется.

## Неподдерживаемые geometry types

`LineString`, `MultiPoint`, `MultiLineString`, `Polygon`, `MultiPolygon`, `GeometryCollection` не преобразуются в points. Centroid/representative point не вычисляется.

`geojson_summary` отражает число skipped non-Point geometries, unlocated/invalid features и invalid points.

## Compressed GeoJSON

`.geojson.gz` проходит single-member bounded gzip path. Dataset сохраняет compressed-source SHA-256 и compression provenance; Point Observations переиспользуют dataset Snapshot.

## Provenance

Каждый Point Observation фиксирует:

```text
source URL
parent dataset Observation ID
Snapshot ID
Feature index
extractor version = geojson-point/1
axis order = longitude_latitude
CRS = WGS84_CRS84
source_declared=true
```

Этот слой не выполняет geocoding, reverse geocoding, geometry repair, CRS transformation или business interpretation.
