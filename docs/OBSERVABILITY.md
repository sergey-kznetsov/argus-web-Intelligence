# ARGUS operational observability

ARGUS ships a free, dependency-light observability layer in addition to structured JSON logs. The built-in layer is designed for TEST and production diagnostics without requiring Prometheus, OpenTelemetry, SaaS monitoring, or another paid service.

## Surfaces

### API process

Authenticated endpoint:

`GET /v1/operations/metrics`

The endpoint returns:

- the API process `OperationalMetrics` snapshot;
- PostgreSQL queue state when `ARGUS_EXECUTION_ROLE=api`;
- execution role and storage backend;
- explicit exporter state.

The endpoint uses the same Bearer dependency as the other internal operational API routes.

### Worker process

The localhost worker probe additionally exposes:

`GET /metricsz`

This returns only the worker process metric registry. It is intentionally separate from `/readyz` and `/healthz` so health probes do not need to parse operational counters.

The universal module manifest binds the worker probe to localhost by default. `/metricsz` must not be exposed directly to the public Internet.

## Metric families

The built-in registry records bounded counters, duration aggregates, and gauges.

Collection/queue signals include:

- `collections_accepted_total`;
- `collection_submission_rejected_total`;
- `collection_queue_wait_seconds`;
- `collection_duration_seconds`;
- `collections_finished_total`;
- `collections_running`.

Source/runtime signals include:

- `source_discovery_total`;
- `source_discovered_tasks_total`;
- `source_discovery_duration_seconds`;
- `source_fetch_total`;
- `source_fetch_duration_seconds`;
- `source_extract_total`;
- `source_extract_duration_seconds`;
- `source_result_total`;
- `source_observations_total`;
- `source_evidence_total`;
- `source_retryable_errors_total`;
- `runtime_escalation_total`.

Persistence signals include:

- `atomic_commit_duration_seconds`;
- `db_operation_duration_seconds`;
- `atomic_commit_errors_total`;
- `observations_committed_total`;
- `evidence_committed_total`;
- `snapshots_committed_total`.

Worker/recovery signals include:

- `worker_starts_total` / `worker_stops_total`;
- `worker_claims_total`;
- `worker_active_collections`;
- `worker_concurrency_limit`;
- `worker_collection_tasks_total`;
- `worker_lease_renewals_total`;
- `worker_lease_losses_total`;
- `worker_execution_cancelled_after_lease_loss_total`;
- `worker_registration_recoveries_total`;
- `worker_heartbeats_total`;
- `retention_passes_total`;
- `retention_rows_removed_total`.

PostgreSQL queue depth and worker freshness remain authoritative database-derived values returned by the queue operations surface. They are not recreated as process-local counters.

## Duration representation

A duration series stores:

- count;
- total seconds;
- average seconds;
- maximum seconds;
- last seconds.

The built-in registry deliberately does not implement unbounded raw timing samples.

## Cardinality policy

Operational metrics must never become a second request log.

Each metric is limited to 128 label series and six labels per series. Excess series are counted in `dropped_series` rather than allocated indefinitely.

The following request-specific labels are rejected at write time:

- `analysis_id`;
- `collection_id`;
- `consumer`;
- `entity_id`;
- `evidence_id`;
- `observation_id`;
- `request_id`;
- `source_url`;
- `url`;
- `worker_id`.

Stable dimensions such as `source_id`, runtime, status, operation, reason category, and execution mode are allowed.

This is enforced by `OperationalMetrics`; it is not merely a caller convention.

## Process boundaries

Metrics are process-local by design.

The API process does not pretend to contain FAST/BROWSER/AGENT timings produced inside independent worker processes. Worker process metrics are available through each worker localhost `/metricsz` probe. PostgreSQL queue state provides the shared cluster-level operational view.

A future Prometheus or OpenTelemetry exporter can expose the same registry without changing factual collection logic. Those exporters are optional and are currently reported as disabled by the API. No paid monitoring dependency is required for ARGUS operation.

## Structured logs

`ArgusJsonFormatter` continues to provide event-oriented diagnostic logs. Logs may include request identifiers after redaction because they are diagnostic events, not metric labels. Metrics intentionally use a stricter cardinality rule.

Together the two surfaces serve different purposes:

- metrics answer "how often/how long/how many";
- logs answer "what happened to this specific execution".

## Stage 10 operational contract

The Stage 10 implementation therefore covers:

- throughput and collection completion;
- queue wait and PostgreSQL queue state;
- collection duration;
- source success, partial and blocked outcomes;
- FAST/BROWSER/AGENT escalation visibility through runtime labels;
- retryable source error counts;
- lease renewal/loss and registration recovery;
- PostgreSQL/atomic-commit timing;
- browser/agent source failures through source/runtime outcomes;
- committed snapshot volume;
- retention activity;
- bounded-cardinality enforcement;
- authenticated API metrics and localhost worker metrics.

Prometheus/OpenTelemetry remain optional extension points rather than production dependencies.
