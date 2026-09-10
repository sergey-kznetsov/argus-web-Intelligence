# Извлечение HTML Microdata

ARGUS нормализует явно объявленную HTML Microdata (`itemscope` / `itemprop`) как дополнительный evidence-backed factual layer публичной страницы.

```text
public HTML page
  -> page Snapshot
  -> bounded Microdata extractor
  -> Observation(source_kind=microdata)
  -> Evidence(type=microdata)
```

Обычный `web_page` Observation сохраняется. Microdata дополняет страницу, а не заменяет её.

## Source declarations

Element с `itemscope` создаёт item. Descendant `itemprop`, принадлежащие этому item, собираются в source order. Повторные properties сохраняются как ordered lists.

Поддерживаются правила values:

- nested `itemscope` -> nested item reference;
- `<meta>` -> `content`;
- media elements -> `src`;
- `<a>`, `<area>`, `<link>` -> `href`;
- `<object>` -> `data`;
- `<data>`, `<meter>` -> `value`;
- `<time>` -> `datetime`, если есть;
- остальные elements -> bounded source text текущего item.

Nested items также могут стать отдельными Microdata Observations. Их internal properties не копируются в parent item.

## itemref

Версия 1 не пытается частично интерпретировать `itemref`. Item с `itemref` полностью пропускается и учитывается в `itemref_skipped`.

Это защищает от публикации неполной локальной части как будто она представляет полный source-declared item. Поддержка `itemref` потребует bounded cycle detection и deterministic traversal.

## URL и identity

Relative HTTP/HTTPS values разрешаются от реально fetched page URL. Unsupported/unsafe schemes не переписываются. Удалённое значение делает extraction incomplete (`truncated=true`).

`itemid` может использоваться как entity identity, но никогда не заменяет Evidence URL. `Observation.url` и `Evidence.source.url` остаются фактически fetched page URL.

## Limits

Переиспользуются structured-data settings:

```text
scan chars = min(structured_data_max_bytes, 750000)
items = min(structured_data_max_records, 100)
properties/item = min(structured_data_max_columns, 100)
property chars = structured_data_max_cell_chars
values/repeated property <= 20
item types <= 10
property names per itemprop <= 20
```

Любое clipping/invalid URL/over-budget значение отражается через `truncated`; silent truncation запрещена.

## Observation model

```text
source      = generic_web
source_kind = microdata
entity_type = structured_entity
url         = fetched page URL
entity_id   = source itemid либо stable page-local identity
```

Data содержит `item_types`, `item_id`, properties и truncation state. `title` берётся только из explicit `name`/`headline`; `text` — из `description`/`abstract`; `published_at` — только из parseable explicit `datePublished`.

ARGUS не использует vocabulary knowledge для выдумывания aliases или missing properties.

## Provenance и Evidence

Provenance содержит parent `snapshot_id`, page URL, item index, goals, extractor version, truncation state, `itemref_skipped`, `remote_vocabularies_resolved=false`.

Evidence содержит bounded canonical JSON excerpt и canonical SHA-256 для связи excerpt с полной normalized entity и исходным HTML Snapshot.

## Не входит в текущий контракт

Microdata v1 не:

- fetch'ит remote vocabularies;
- валидирует бизнес-семантику schema.org;
- выполняет `itemref` traversal;
- выводит отсутствующие properties;
- объединяет JSON-LD/Microformats2/Microdata в одну business entity;
- оценивает истинность source claims;
- формирует выводы Kraken/Janus.
