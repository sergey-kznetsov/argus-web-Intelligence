# Выдача результатов ARGUS

`GET /v1/collections/{collection_id}/result` сохраняется для небольших collections, но API не загружает произвольно большой result в память.

## Full-result gate

Полный result возвращается только если одновременно соблюдены:

```text
ARGUS_API_FULL_RESULT_MAX_ITEMS=100
ARGUS_API_FULL_RESULT_MAX_BYTES=4194304
```

Items считаются как observations + evidence; byte limit основан на stored JSON bytes этих rows.

Перед загрузкой API проверяет counts/bytes в storage. При превышении любого лимита endpoint возвращает HTTP `409`, `detail.code=RESULT_REQUIRES_PAGINATION` и ссылки на summary/observation/evidence page endpoints. Silent truncation отсутствует.

`GET /v1/collections/{collection_id}/result/summary` возвращает status, counts, stored bytes, coverage/errors, delivery limits и `full_result_available`.

## Paged delivery

Для больших terminal results:

```text
GET /v1/collections/{collection_id}/result/observations
GET /v1/collections/{collection_id}/result/evidence
```

Defaults:

```text
ARGUS_API_RESULT_PAGE_DEFAULT_SIZE=50
ARGUS_API_RESULT_PAGE_MAX_SIZE=100
ARGUS_API_RESULT_PAGE_MAX_BYTES=2097152
```

Page ограничивается и item count, и stored JSON bytes. Первый item разрешается вернуть даже если один он превышает byte target, иначе cursor progression мог бы навсегда заблокироваться. Response содержит `page_stored_bytes`.

Paged delivery доступна только для terminal states:

```text
completed
partial
blocked
failed
cancelled
```

Для `queued`/`running` возвращается `409 RESULT_NOT_FINAL`.

## Cursors

Cursor — opaque URL-safe value. Consumer не должен его разбирать или строить самостоятельно.

Cursor связан с:

- collection id;
- result kind (`observation`/`evidence`);
- last delivered item id.

Cursor другого collection/result kind отклоняется `400`.

Storage order стабилен по `observation_id ASC` / `evidence_id ASC`.

## PostgreSQL consistency

Read side использует отдельный небольшой Psycopg async pool. Summary/full/page reads выполняются в `REPEATABLE READ READ ONLY`, поэтому retention не должен дать mixed response из состояния до/после cleanup.

## Compatibility

Protocol `1.0.0` `CollectionResult` для малых collections сохраняется. Consumer обязан обрабатывать `409 RESULT_REQUIRES_PAGINATION` для больших results и переходить к paged endpoints. Сервер не маскирует silent partial result под успешный полный ответ.
