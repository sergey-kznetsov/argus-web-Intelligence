# Нормализация factual types Schema.org

ARGUS извлекает source-declared structured entities из embedded JSON-LD и HTML Microdata. Raw structured payload остаётся Evidence, а schema.org normalization добавляет консервативные factual categories и selected source-declared fields в Observation.

## Boundary

ARGUS не загружает schema.org definitions в runtime, не разрешает remote vocabularies и не добавляет consumer-specific meaning.

Нормализация использует только:

- explicit schema.org type URLs, например `https://schema.org/Review`;
- simple type token вроде `Review` только если source явно объявляет schema.org `@context`/`@vocab`.

Unknown vocabulary остаётся `structured_entity`.

## Текущие factual categories

```text
Schema.org Review family      -> review
Article/NewsArticle/reporting -> publication
Comment                       -> comment
Dataset/DataCatalog           -> dataset
Event/*Event                  -> event
Organization/*Organization    -> organization
Person                        -> person
Place                         -> place
Product                       -> product
Service                       -> service
```

Recognized schema.org type без mapping остаётся `structured_entity`. ARGUS не fetch'ит live schema hierarchy для subclass discovery.

## JSON-LD context

`EmbeddedJsonLdExtractor` сохраняет bounded context hints:

- string `@context`;
- string values в context array;
- explicit `@vocab` из bounded context object.

Для `@graph` child entities наследуют root context hint, если не объявили свой. Context URL не dereference'ится.

Original JSON-LD остаётся в Observation data. Provenance содержит recognized schema types, normalized entity type, context hints и `remote_vocabularies_resolved=false`.

## Text и publication date

Для recognized schema.org entity ARGUS может заполнить Observation text только из source-declared fields:

```text
Review      reviewBody -> description
publication articleBody -> text -> description
Comment     text -> description
other mapped categories -> description
```

`datePublished` может заполнить `Observation.published_at` только как valid ISO-style date/datetime. Даты не выводятся из prose, event start time не подставляется как publication time.

Provenance/Evidence metadata фиксирует source field.

## Coordinates

Поддерживаются source-declared:

- JSON-LD `Place.geo.latitude/longitude`;
- direct `latitude/longitude` у recognized schema.org entity;
- Microdata GeoCoordinates/recognized entity с explicit coordinates.

Values могут быть numeric или numeric text. Оба значения должны быть finite в WGS84 ranges. ARGUS не меняет оси, не geocode'ит missing location и не чинит malformed values.

Invalid declared coordinates остаются raw Evidence с `geospatial_valid=false`; `Observation.geo` остаётся empty. Provenance: `geocoding_used=false`.

## Microdata

Для Microdata schema recognition применяется только к explicit schema.org `itemtype` URLs; hidden default vocabulary нет.

## Stable identity

`entity_type` участвует в deterministic Observation identity. Если schema normalization меняет `structured_entity` на более specific factual type, Observation ID и linked Evidence ID пересчитываются под normalized model.

Display text, publication date или coordinates отдельно не создают вторую identity, потому что raw structured payload уже представлен content hash.

## Не входит в этот слой

Он не:

- оценивает/сентиментит review;
- решает важность publication;
- выводит organization categories для business analytics;
- расширяет arbitrary JSON-LD contexts;
- выполняет ontology reasoning;
- geocode'ит missing coordinates;
- принимает consumer-specific решения.
