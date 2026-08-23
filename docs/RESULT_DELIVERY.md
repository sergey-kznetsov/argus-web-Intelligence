# ARGUS result delivery

ARGUS keeps the original `GET /v1/collections/{collection_id}/result` response for small collections, but never loads an arbitrarily large result into the API process.

## Full-result gate

A full result is returned only while both configured limits are satisfied:

- `ARGUS_API_FULL_RESULT_MAX_ITEMS=100` — observations plus evidence;
- `ARGUS_API_FULL_RESULT_MAX_BYTES=4194304` — stored JSON bytes for observations plus evidence.

The API checks counts/bytes in storage before loading result rows. If either limit is exceeded, the endpoint returns HTTP `409` with `detail.code=RESULT_REQUIRES_PAGINATION` and the URLs of the summary, observation-page and evidence-page endpoints. Data is not silently truncated.

`GET /v1/collections/{collection_id}/result/summary` returns collection status, observation/evidence counts, stored byte count, coverage/errors and the current delivery limits. Consumers can use `full_result_available` to decide whether the legacy full-result path is safe.

## Paged delivery

Large terminal results are read through:

- `GET /v1/collections/{collection_id}/result/observations`;
- `GET /v1/collections/{collection_id}/result/evidence`.

Defaults:

- `ARGUS_API_RESULT_PAGE_DEFAULT_SIZE=50`;
- `ARGUS_API_RESULT_PAGE_MAX_SIZE=100`;
- `ARGUS_API_RESULT_PAGE_MAX_BYTES=2097152`.

Every page is bounded by both item count and stored JSON bytes. One first item is allowed even when that individual row exceeds the byte target; otherwise a single valid large item could permanently block cursor progress. The response reports `page_stored_bytes` so consumers can observe the actual page payload basis.

Paged delivery is exposed only for terminal collection states (`completed`, `partial`, `blocked`, `failed`, `cancelled`). A `queued` or `running` collection returns `409 RESULT_NOT_FINAL`. This keeps the keyset traversal stable rather than allowing new result rows to appear between pages.

## Cursor properties

Result cursors are opaque URL-safe values. Consumers must not parse or construct them. They return the cursor received from the previous page unchanged.

Each cursor is bound to:

- the collection id;
- the result kind (`observation` or `evidence`);
- the last delivered item id.

A cursor from another collection or from the other result kind is rejected with HTTP `400`. This prevents accidental cross-stream traversal.

Storage keyset order is stable by `observation_id ASC` or `evidence_id ASC`. PostgreSQL uses composite `(collection_id, item_id)` indexes; SQLite uses the equivalent indexed primary-id traversal.

## PostgreSQL consistency

The server read side uses its own small Psycopg async pool and does not reuse worker write transactions. Result summary/full/page reads run in `REPEATABLE READ READ ONLY` transactions.

This matters for retention: if a terminal collection becomes old enough for cleanup while an API response is being assembled, one response sees one consistent database snapshot instead of a collection row from before cleanup and child rows from after cleanup.

## Compatibility

The Protocol `1.0.0` `CollectionResult` model is unchanged. Existing Kraken/Janus clients that receive small results can continue to use `/result` exactly as before.

Clients must handle `409 RESULT_REQUIRES_PAGINATION` for larger collections and then consume the documented paged endpoints. The server never substitutes a partial/truncated `CollectionResult` while claiming success.
