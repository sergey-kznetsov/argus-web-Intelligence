# Recovery и replay ARGUS worker

Server execution ARGUS использует at-least-once network execution с атомарной durable boundary для task. Незавершённый `SourceTask` может повториться после process failure, lease expiry, PostgreSQL interruption или остановки stale worker, но успешная публикация одного task фиксируется одной database transaction.

## Основной recovery contract

- `pending_tasks` и `visited` хранятся в collection checkpoint;
- factual adapters после recovery могут повторить network read;
- Snapshots сначала staging'уются в task-local memory;
- успешная task transaction вместе пишет Snapshot, Observation, Evidence и checkpoint;
- child tasks и historical branch checkpoint входят в тот же collection-state commit;
- при transaction failure новые factual rows и `visited` не становятся durable;
- task, отсутствующий в persisted `visited`, безопасно replay'ится;
- новая Collection создаёт новую collection-scoped Snapshot identity даже при неизменном source content.

## Atomic task commit

```text
fetch / extract / normalize
  -> stage Snapshot(s)
  -> prepare Observation + Evidence + child tasks
  -> BEGIN
      lock collection row
      verify lease owner (server mode)
      persist Snapshot(s)
      upsert Observation(s)
      upsert Evidence
      persist visited/pending/coverage checkpoint
  -> COMMIT
```

Stop до COMMIT оставляет прежнее durable состояние. Stop после COMMIT оставляет факты и `visited`, поэтому replacement worker не fetch'ит этот task повторно.

Terminal state (`completed`, `partial`, `blocked`, `failed`) может записываться после последнего task transaction. Если worker падает между task commit и finalization, recovery видит `visited` и только завершает collection state.

SQLite embedded mode использует ту же logical boundary в SQLite transaction. PostgreSQL server mode дополнительно применяет lease fencing.

## Lease fencing

`argus.collection_leases` — authority server execution.

Worker устанавливает `LeaseFence(collection_id, worker_id)` вокруг `CollectionOrchestrator.execute()`. Перед factual publication transaction проверяет:

- collection существует и не cancelled;
- текущий worker владеет lease;
- lease не expired.

Если другой worker получил expired lease, старый не может записать поздние collection state/factual rows. Storage поднимает `LeaseLostError`, и stale execution завершается по cancellation semantics без превращения collection в source failure.

Heartbeat — второй уровень защиты: неуспешное lease renewal отменяет local execution task.

## Graceful shutdown

При shutdown worker:

1. перестаёт брать новые claims;
2. отменяет active collection tasks;
3. ждёт unwind cancellation;
4. освобождает collection lease;
5. unregister'ит worker instance;
6. закрывает resources.

Replacement worker может получить collection сразу после release, не дожидаясь lease timeout.

Cancellation передаётся FAST, BROWSER и активному AGENT path. Отменённый FAST request не запускает BROWSER fallback, отменённый BROWSER request — AGENT fallback. Общий LLM gate и дочерний Browser Use process освобождаются при unwind; cancellation не превращается в source failure.

## Сбой PostgreSQL во время worker execution

Storage error внутри lease-owned call — не source error. `FencedPostgresRepository` превращает retryable worker-storage failure в `WorkerStorageError`, что прекращает текущую попытку и оставляет task replayable из последнего durable checkpoint.

Heartbeat DB failure также отменяет active execution. Другой worker не должен красть lease до фактического release/expiry.

## Source error и persistence error

Обычный fetch/extraction source failure записывается как `SOURCE_ERROR` по collection semantics; staged Snapshot не публикуется.

Atomic task commit failure не маркируется как `SOURCE_ERROR`. Lease loss/DB interruption прекращают worker attempt, чтобы task можно было повторить.

Historical branch expansion изолирована от factual extraction: если factual result уже получен, а optional historical branch упал, ARGUS может записать `HISTORICAL_BRANCH_ERROR` и всё равно атомарно опубликовать доказанный factual result.

## Historical branch recovery

Historical expansion входит в durable checkpoint parent task. При успешном commit вместе фиксируются:

- parent Observation/Evidence;
- parent `visited`;
- consumed `historical_branch_queries`;
- branch tasks в `pending_tasks`;
- depth/provenance metadata.

После такого commit recovery не повторяет branch discovery parent task, а исполняет persisted child task.

## Изменение source между попытками

Network reads не transactional. Source может измениться между failure и replay.

Если первая попытка не дошла до COMMIT, её payload не публикуется; replacement worker может получить более новую версию и опубликовать её вместе с checkpoint. Если первая попытка успела COMMIT, `visited` зафиксирован вместе с facts и повторного fetch нет.

Collection-scoped Snapshot IDs детерминированы по collection/source/URL/content hash/extractor version.

## SiteRecipe scope

SiteRecipe — shared operational state, а не collection factual output, поэтому recipe lifecycle не входит в task factual transaction. Active recipe может пережить interrupted collection, но сам recipe не является Observation/Evidence.

Активный AGENT может создать только candidate recipe. Candidate становится active после deterministic BROWSER replay и source-backed проверки цели; interruption или blocked replay оставляет его отклонённым/неактивным. Recipe и model output не входят в factual Evidence.

## Проверяемые fault scenarios

Automated suite покрывает, среди прочего:

- concurrent claims разных collections;
- lease transfer при blocked fetch старого worker;
- graceful shutdown с параллельно polling replacement worker;
- PostgreSQL error при lease heartbeat;
- failed atomic commit без публикации Snapshot/Observation/Evidence/visited;
- запрет stale commit после lease transfer;
- cancellation после task commit до final collection state;
- historical branch resume без повторного discovery;
- FAST cancellation без BROWSER escalation;
- PostgreSQL pool recovery после terminated backend connection.

Deployment-level validation остаётся отдельным TEST smoke: реальные restart PostgreSQL/ARGUS processes, lease expiry/readiness recovery и end-to-end completion через consumer module.
