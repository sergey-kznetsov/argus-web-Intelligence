# Извлечение геоданных KML и KMZ

ARGUS поддерживает factual KML Point и KMZ packages без отдельного XML parser и без универсальной распаковки архивов.

## Pipeline

Обычный KML:

```text
HTTP response
  -> bounded structured-data XML parser
  -> source XML dataset Observation/Evidence
  -> KML Placemark/Point normalization
  -> geospatial_feature Observation + kml_point Evidence
```

Gzip KML:

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
  -> только root doc.kml
  -> bounded XML parser
  -> shared KML normalizer
```

Fetched source URL остаётся factual source URL. Для KMZ SHA-256 package остаётся identity исходного документа, а provenance отдельно фиксирует SHA-256 `doc.kml`.

## Поддерживаемый KML subset

Текущий контракт намеренно ограничен:

- `Placemark`;
- direct `Point` geometry;
- `name`;
- `description`;
- `coordinates` с одним 2D/3D tuple.

Порядок координат:

```text
longitude,latitude[,altitude]
```

Longitude должен быть в `[-180, 180]`, latitude — в `[-90, 90]`, числа должны быть finite. ARGUS не меняет оси, не чинит координаты, не выводит location из текста и не геокодирует invalid Point.

Altitude сохраняется как source-declared data, но не интерпретируется.

Каждый accepted Point создаёт:

- `source_kind=kml_point`;
- `entity_type=geospatial_feature`;
- `Observation.geo`;
- source-declared coordinate tuple в data;
- bounded canonical Placemark Evidence;
- parent XML/KMZ Snapshot ID и dataset Observation ID в provenance.

## Unsupported geometry

`LineString`, `LinearRing`, `Polygon`, `MultiGeometry`, `Model` не превращаются в points. ARGUS не вычисляет centroid или representative point. Исходная geometry остаётся в bounded XML dataset, а KML summary показывает число skipped Placemarks.

## NetworkLink

KML `NetworkLink` не открывается в процессе normalization. Source-declared data может остаться в XML Evidence, но сам KML extractor делает zero network requests по этим ссылкам.

Любой последующий public URL должен пройти обычный ARGUS discovery/source/URL-security path.

## Limits

KML использует существующие structured-data limits:

- source bytes;
- XML node/depth/string/container limits;
- `kml_max_placemarks`, связанный с `ARGUS_STRUCTURED_DATA_MAX_RECORDS`.

При превышении Placemark budget bounded facts сохраняются, dataset/result получает partial, ошибка — `KML_EXTRACTION_TRUNCATED`.

## KMZ security

KMZ считается недоверенным ZIP package и никогда не распаковывается на диск.

Preflight отклоняет:

- package сверх compressed byte limit;
- слишком много members;
- total declared uncompressed size сверх лимита;
- отдельный member сверх лимита;
- encrypted members;
- symbolic links;
- unsupported ZIP compression;
- absolute/traversal/backslash/NUL/drive-like paths;
- duplicate/case-colliding names;
- отсутствие root `doc.kml`.

Читается только root `doc.kml`. Images/overlays/другие resources проверяются по package limits, но не разрешаются и не render'ятся.

Default bounds выводятся из structured-data budget:

```text
compressed KMZ <= ARGUS_STRUCTURED_DATA_MAX_BYTES
members <= min(records limit, 1000)
total uncompressed <= min(4x byte budget, 20 MiB)
member <= min(2x byte budget, 10 MiB)
doc.kml <= structured byte budget
```

## Provenance

KMZ dataset и Point facts сохраняют package SHA-256/size, member count, total uncompressed declaration, `root_kml=doc.kml`, SHA-256/size `doc.kml`, extractor version, `resources_resolved=false`, `network_links_followed=false`.

## Не входит в текущий слой

ARGUS не render'ит карту, не скачивает resources из KMZ, не исполняет KML tours/overlays, не следует NetworkLink, не вычисляет centroids и не принимает consumer-specific решения.
