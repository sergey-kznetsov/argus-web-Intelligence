# Наблюдаемость ARGUS

ARGUS содержит встроенный dependency-light observability layer поверх structured JSON logs. Он рассчитан на TEST/production диагностику без обязательного Prometheus, OpenTelemetry или SaaS monitoring.

## API process

Authenticated endpoint:

```text
GET /v1/operations/metrics
```

Он возвращает:

- snapshot process-local `OperationalMetrics`;
- PostgreSQL queue state для `ARGUS_EXECUTION_ROLE=api`;
- execution role и storage backend;
- состояние optional exporter'ов.

Endpoint использует тот же Bearer auth, что и остальные operational routes.

## Worker process

Loopback worker probe дополнительно отдаёт:

```text
GET /metricsz
```

Это process-local registry worker. `/metricsz` отделён от `/readyz` и `/healthz`, чтобы health probes не разбирали operational counters. Worker metrics endpoint не должен публиковаться напрямую в Internet.

## Metric families

Collection/queue:

```text
collections_accepted_total
collection_submission_rejected_total
collection_queue_wait_seconds
collection_duration_seconds
collections_finished_total
collections_running
```

Sources/runtime:

```text
source_discovery_total
source_discovered_tasks_total
source_discovery_duration_seconds
source_fetch_total
source_fetch_duration_seconds
source_extract_total
source_extract_duration_seconds
source_result_total
source_observations_total
source_evidence_total
source_retryable_errors_total
runtime_escalation_total
```

Persistence:

```text
atomic_commit_duration_seconds
db_operation_duration_seconds
atomic_commit_errors_total
observations_committed_total
evidence_committed_total
snapshots_committed_total
```

Worker/recovery:

```text
worker_starts_total
worker_stops_total
worker_claims_total
worker_active_collections
worker_concurrency_limit
worker_collection_tasks_total
worker_lease_renewals_total
worker_lease_losses_total
worker_execution_cancelled_after_lease_loss_total
worker_registration_recoveries_total
worker_heartbeats_total
retention_passes_total
retention_rows_removed_total
```

Queue depth и worker freshness из PostgreSQL остаются authoritative shared values и не дублируются как process-local counters.

## Duration representation

Duration series хранит count, total seconds, average, maximum и last value. Raw timing samples без ограничений не накапливаются.

## Cardinality policy

Metrics не должны становиться вторым request log. Один metric ограничен 128 label series и шестью labels на series. Excess series учитываются через `dropped_series`.

Request-specific labels запрещены:

```text
analysis_id
collection_id
consumer
entity_id
evidence_id
observation_id
request_id
source_url
url
worker_id
```

Допустимы стабильные dimensions вроде `source_id`, runtime, status, operation, reason category, execution mode.

## Process boundaries

Metrics process-local. API process не притворяется владельцем source timings, созданных worker process. Worker metrics читаются на его loopback `/metricsz`; PostgreSQL queue state даёт shared operational view.

Metric names сохраняют возможность runtime labels для разных retrieval layers. В текущем production graph AGENT не подключён, поэтому фактическая AGENT telemetry не должна ожидаться до повторного включения этого runtime.

## Structured logs

`ArgusJsonFormatter` остаётся event-oriented diagnostic log. Logs после redaction могут содержать request identifiers, потому что это диагностические события, а не metric labels.

Metrics отвечают «сколько/как часто/как долго», logs — «что произошло в конкретном execution».

## Exporters

Prometheus/OpenTelemetry остаются optional extension points и сейчас не требуются для работы ARGUS. Built-in API явно показывает состояние exporter'ов.
