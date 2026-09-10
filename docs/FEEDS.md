# Извлечение фактов из RSS и Atom

ARGUS рассматривает RSS 2.x и Atom как factual publication sources. Feed discovery остаётся навигацией; entry становится Observation только после получения и безопасного разбора самого feed document.

## Security boundary

Feed XML считается недоверенным input.

Перед semantic parsing выполняется streaming preflight через `defusedxml.iterparse` с явными node/depth budgets. Elements очищаются во время preflight. Только feed, прошедший эту проверку, разбирается повторно по RSS/Atom semantics.

Это даёт две независимые защиты:

- entity/DTD attacks отклоняет `defusedxml`;
- чрезмерно глубокая или широкая XML-структура отклоняется до построения полного semantic tree.

Ошибки нормализуются:

```text
FEED_XML_INVALID
FEED_XML_LIMIT_EXCEEDED
```

External entities, schemas, XInclude и remote resources не разрешаются.

## Runtime limits

RSS/Atom переиспользует structured-data budget:

```text
max_items = min(ARGUS_STRUCTURED_DATA_MAX_RECORDS, 100)
XML nodes = ARGUS_STRUCTURED_DATA_MAX_JSON_NODES
XML depth = ARGUS_STRUCTURED_DATA_MAX_JSON_DEPTH
title/identifier limits <- ARGUS_STRUCTURED_DATA_MAX_CELL_CHARS
entry body <= 100000 chars и дополнительно ограничен cell budget
```

При достижении лимита возвращаются bounded entries с `partial=true` и `FEED_EXTRACTION_TRUNCATED`. Truncation фиксируется также в Observation/Evidence metadata.

## Atom links

Для Atom entry ARGUS предпочитает link с `rel=alternate` либо без `rel`; по RFC 4287 отсутствие `rel` означает `alternate`. `self` и другие links используются только как fallback, если alternate отсутствует.

Принимаются только HTTP/HTTPS entry URL без embedded credentials. Unsafe/invalid URL не используется как factual destination; adapter сохраняет fetched feed URL.

## Source-declared geography

Поддерживаются две формы source-declared point:

- GeoRSS Simple `georss:point`;
- GeoRSS GML `georss:where/gml:Point/gml:pos`.

Порядок GeoRSS: `latitude longitude`. Координаты принимаются только как finite WGS84 values. ARGUS не геокодирует и не угадывает замену malformed point.

Валидный point попадает в `Observation.geo`; provenance сохраняет representation и `geocoding_used=false`. Невалидный явно объявленный point остаётся в `data.geospatial`, `quality.geospatial_valid=false`, а `Observation.geo` остаётся пустым.

Текущий contract нормализует только points. Line/box/polygon не преобразуются в point.

## Evidence и provenance

Каждый bounded feed entry создаёт:

- `Observation` с `source_kind=feed_entry`, `entity_type=publication`;
- один `Evidence` из fetched feed URL;
- общий feed Snapshot ID;
- feed format (`rss`/`atom`);
- entry index и total entries;
- XML node/depth statistics;
- truncation flags;
- GeoRSS metadata, если они source-declared.

Snapshot создаётся из fetched source document до semantic normalization.

## Ограничения

ARGUS не исполняет HTML/JavaScript из feed fields, не dereference'ит entry URLs во время feed normalization и не выводит publication dates из текста. Используются только source-declared RSS/Atom date elements.
