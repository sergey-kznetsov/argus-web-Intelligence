# JSON Feed

ARGUS поддерживает JSON Feed 1.0 и 1.1 как factual public syndication source наряду с RSS/Atom.

JSON Feed — source data, а не search result или аналитический вывод. Items становятся evidence-backed publication Observations; interpretation остаётся consumer module.

## Discovery

Generic Web может обнаружить JSON Feed через стандартный alternate link:

```html
<link rel="alternate" type="application/feed+json" href="/feed.json">
```

URL разрешается относительно page URL, fragment удаляется, применяются те же allowed/denied domain boundaries.

`application/json` не принимается как feed-autodiscovery MIME type, потому что это слишком общий тип для произвольных JSON API. Для autodiscovery нужен `application/feed+json`.

Explicit seed URLs используют узкую filename heuristic (`feed.json`, `*.feed.json`, `*.jsonfeed`), чтобы обычный JSON dataset не дублировался как publication feed.

## Parsing и limits

JSON Feed использует `BoundedStructuredDataExtractor`, поэтому до semantic feed handling действуют:

- byte limit;
- strict UTF-8 decoding;
- rejection NaN/Infinity;
- node/depth/container/string limits;
- отсутствие parser network access.

После bounded parsing требуется:

- root object;
- `version` = `https://jsonfeed.org/version/1` или `https://jsonfeed.org/version/1.1`;
- непустой string `title`;
- array `items`.

Нормализуется максимум 100 items и не больше `ARGUS_STRUCTURED_DATA_MAX_RECORDS`. Достижение лимита даёт `partial=true` + `JSON_FEED_ITEM_LIMIT`.

## Item normalization

Usable item требует:

- `id` как string или JSON number;
- хотя бы один непустой `content_text` или `content_html`.

Invalid items пропускаются с `JSON_FEED_ITEM_INVALID`, а source result становится partial.

Для Observation text предпочтителен `content_text`. Если есть только `content_html`, ARGUS извлекает inert plain text через BeautifulSoup, удаляя script/style/noscript/svg. HTML не исполняется.

Item `url` разрешается относительно feed URL и принимается только как HTTP(S) без URL userinfo. SSRF validation применяется, когда URL позже реально fetch'ится как task; сам adapter автоматически его не открывает.

## Identity, Evidence и provenance

Каждый valid item создаёт:

- `entity_type=publication`;
- `source_kind=json_feed_item`;
- stable Observation identity из collection/source/item id/item URL/canonical item hash;
- canonical source item JSON в `Observation.data.item`;
- bounded canonical JSON как `json_feed_item` Evidence;
- feed Snapshot ID;
- feed URL, item URL, version, goals и extractor version в provenance.

Content hash строится из canonical source JSON, а не rendered plain text.

## Ограничения

Adapter не:

- исполняет embedded HTML/scripts;
- автоматически следует `next_url`;
- интерпретирует authors/tags как business meaning;
- считает любой `application/json` feed'ом;
- обходит authentication/CAPTCHA/access control.
