# ARGUS worker recovery and replay

ARGUS server execution uses an at-least-once network execution model with an atomic durable task boundary. A worker may repeat an unfinished `SourceTask` after process failure, lease expiry, PostgreSQL interruption, or cancellation of a stale worker, but one successful task publication is committed as one database transaction.

The recovery contract is intentionally evidence-safe:

- `pending_tasks` and `visited` are persisted in the collection checkpoint;
- factual adapters may perform network reads more than once after recovery;
- Snapshots produced during extraction are staged in task-local memory instead of being published immediately;
- a successful task transaction writes staged Snapshots, Observation rows, Evidence rows and the updated collection checkpoint together;
- child tasks and historical-branch checkpoint changes are part of that same collection-state commit;
- if the transaction fails, none of those factual rows or the new `visited` marker become durable;
- a task absent from persisted `visited` is therefore safe to replay;
- a genuinely new collection still creates a new Snapshot identity even when source content is unchanged.

## Atomic task commit

The durable success boundary is:

```text
fetch / extract / normalize
        |
        v
stage Snapshot(s) in memory
        |
        v
prepare Observation + Evidence + child tasks
        |
        v
BEGIN DATABASE TRANSACTION
        |
        +--> lock collection row
        +--> verify active lease owner (server mode)
        +--> persist Snapshot(s)
        +--> upsert Observation(s)
        +--> upsert Evidence
        +--> persist visited/pending/coverage checkpoint
        |
        v
COMMIT
```

A process stop before `COMMIT` leaves the previous durable checkpoint and factual state unchanged. A process stop after `COMMIT` leaves both facts and `visited` durable, so the replacement worker skips that completed task.

This removes the former crash window where Observation/Evidence could be durable while the corresponding task was still absent from `visited`.

SQLite embedded mode uses the same logical boundary in one SQLite transaction. PostgreSQL server mode additionally fences the transaction by the current worker lease.

## Lease fencing

`argus.collection_leases` is the ownership authority for server execution.

The worker installs a `LeaseFence(collection_id, worker_id)` context around `CollectionOrchestrator.execute()`. The product PostgreSQL repository checks that context on collection mutations. The atomic task transaction locks the collection row first and then the lease row, matching the claim lock order and avoiding an inverse-lock deadlock with `claim_next_collection`.

Before factual publication the transaction verifies that:

- the collection still exists and is not cancelled;
- the current worker still owns the collection lease;
- the lease is not expired.

If a lease expires and another worker claims the collection, the old worker cannot publish later state or factual rows even during the interval before its next heartbeat notices the loss. The storage layer raises `LeaseLostError`, which uses cancellation semantics so the stale attempt stops without converting the collection into a terminal source failure.

The heartbeat loop remains a second line of defense: a failed lease renewal also cancels the local execution task.

## Storage interruption during worker execution

A PostgreSQL transport/storage error inside a lease-owned storage call is not a source error. `FencedPostgresRepository` converts the retryable worker-storage failure path into `WorkerStorageError` and logs the failed operation. This cancellation-style signal bypasses source-error handling, prevents the current task from becoming durably `visited`, and releases or eventually expires the lease when the worker attempt exits.

A later claim can therefore replay the last durable checkpoint after PostgreSQL is usable again.

Storage reads used by factual adapters are included in this behavior, including collection state, Observation/Evidence result reads, temporal snapshot lookup and SiteRecipe reads/writes. API/admin calls have no worker lease context and preserve normal PostgreSQL exception behavior.

## Source errors versus persistence errors

Source failures and durable-state failures are deliberately separated.

A normal source/fetch/extraction exception is recorded as `SOURCE_ERROR`; the failed task is checkpointed according to collection source-error semantics and no staged Snapshot is published.

An atomic task commit failure is outside the source-error handler. It is never relabelled as `SOURCE_ERROR`. In server mode a lost lease or retryable PostgreSQL interruption aborts the worker attempt so the task remains replayable from the previous durable checkpoint.

Historical branch expansion is also isolated from factual extraction. If factual extraction succeeded but optional historical expansion raises an ordinary exception, ARGUS records `HISTORICAL_BRANCH_ERROR` and can still atomically publish the already proven factual result rather than discarding it.

## Content changes between failure and replay

Network reads are not transactional. A source can change between two attempts.

With the atomic durable boundary, an interrupted first attempt that never committed does not publish its old payload. The replacement worker may fetch a newer payload and atomically publish that newer state with the task checkpoint. ARGUS therefore does not create two durable factual states merely because a source changed inside an uncommitted crash window.

If the first attempt committed before the process stopped, its `visited` marker committed in the same transaction and the task is not replayed. A later independent collection can still observe the changed source as a legitimate new temporal state.

Collection-scoped Snapshot IDs remain deterministic from collection, source, URL, content hash and extractor version. They provide stable identity for retries and provenance; the transaction boundary provides the stronger publication guarantee.

## SiteRecipe scope

SiteRecipe state is shared operational state rather than collection output. Recipe success/failure/candidate state may be updated during deterministic or agent-assisted navigation before factual task commit. It is intentionally not part of the collection factual transaction.

A recipe can therefore survive an interrupted collection attempt, but it cannot itself become Observation or Evidence. Factual output still requires a successful source task transaction.

## Scope and guarantees

The atomic boundary covers ARGUS-owned collection output and checkpoint state:

- staged temporal Snapshots;
- Observation;
- Evidence;
- source coverage attached to the collection record;
- `visited`;
- `pending_tasks` generated by the task;
- historical branch checkpoint state.

External HTTP requests, browser navigation and third-party public websites remain outside the database transaction and can be repeated.

The design does not require consumer-specific logic. Kraken, Janus and future consumers receive the same factual recovery semantics.
