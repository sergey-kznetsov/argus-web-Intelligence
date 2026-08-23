# ARGUS worker recovery and replay

ARGUS server execution uses an at-least-once collection model. A worker may repeat an unfinished `SourceTask` after process failure, lease expiry, PostgreSQL interruption, or a crash between factual persistence and checkpoint persistence.

The recovery contract is intentionally evidence-safe:

- `pending_tasks` and `visited` are persisted in the collection checkpoint;
- a task is considered completed only after its factual side effects and the following checkpoint update succeed;
- a task that is absent from persisted `visited` may be replayed by the next worker;
- Observation and Evidence IDs are deterministic inside a collection, so unchanged replay upserts the same factual rows;
- collection-scoped Snapshot IDs are deterministic from collection, source, URL, content hash and extractor version, so unchanged replay does not invent another historical timestamp;
- a genuinely new collection still creates a new Snapshot even when source content is unchanged.

## Lease fencing

`argus.collection_leases` is the ownership authority for server execution.

The worker installs a `LeaseFence(collection_id, worker_id)` context around `CollectionOrchestrator.execute()`. The product PostgreSQL repository checks that context on collection mutations. State, Observation, Evidence and collection-scoped Snapshot writes require a matching, non-expired lease in the same SQL statement.

If a lease expires and another worker claims the collection, the old worker cannot commit later state or factual rows even during the interval before its next heartbeat notices the loss. The storage layer raises `LeaseLostError`, which uses cancellation semantics so the stale attempt stops without converting the collection into a terminal failure.

The heartbeat loop remains a second line of defense: a failed lease renewal also cancels the local execution task.

## Storage interruption during worker execution

A PostgreSQL error inside a lease-owned storage call is not a source error. `FencedPostgresRepository` converts such failures into `WorkerStorageError` and logs the failed storage operation. This cancellation-style signal bypasses source-error handling, prevents the current task from being persisted as `visited`, and releases the lease when the worker attempt exits.

A later worker claim can therefore replay the last durable checkpoint after the database is usable again.

Storage reads used by factual adapters are included in this behavior, including collection state, Observation/Evidence result reads, temporal snapshot lookup and SiteRecipe reads/writes. API/admin calls have no worker lease context and preserve normal PostgreSQL exception behavior.

## Crash window after factual persistence

The important recovery window is:

```text
fetch/extract
    |
    v
Snapshot + Observation + Evidence persisted
    |
    X  process stops before checkpoint update
    |
    v
replacement worker loads old checkpoint
    |
    v
same SourceTask is replayed
```

For unchanged source content, deterministic identities make the replay converge on the rows already written by the first attempt. The replacement worker then persists `visited` and continues the collection.

A regression test intentionally interrupts execution at this exact boundary and verifies that recovery finishes with one Observation, one Evidence row and one collection-scoped Snapshot.

## Scope and guarantees

These guarantees apply to ARGUS-owned durable state. Network reads themselves are not transactional and may be repeated. If the public source changes between the failed attempt and replay, ARGUS may observe the newer content as a distinct factual state; it does not pretend that two different source payloads are identical.

SiteRecipe state is shared operational state rather than collection output. It is deterministic/replay-oriented but is not part of the collection exactly-once boundary.

The design does not require consumer-specific logic. Kraken, Janus and future consumers receive the same factual recovery semantics.