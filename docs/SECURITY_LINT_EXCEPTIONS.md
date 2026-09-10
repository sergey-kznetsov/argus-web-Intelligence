# Исключения security lint

Этот файл фиксирует узкие исключения Ruff `S` rules. CI-команда `ruff check src --select S` остаётся обязательной; exceptions задаются per-file и не должны расширяться без security review.

Проверено: 10 сентября 2026 года.

## Dynamic SQL: S608

Files:

```text
src/argus/result_delivery.py
src/argus/storage/fenced_postgres.py
src/argus/storage/postgres.py
src/argus/storage/postgres_migrations.py
src/argus/storage/postgres_operations.py
src/argus/storage/postgres_storage_stats.py
src/argus/storage/sqlite.py
```

SQL values передаются параметрами. Интерполируются только schema/table/id-column identifiers из internal fixed allowlists/constants (`observations`, `evidence`, `observation_id`, `evidence_id`, schema `argus`). Request/URL/consumer/analysis/source text не может стать SQL identifier.

Исключение не разрешает user-controlled SQL fragments. Новый dynamic identifier должен сначала получить explicit fixed allowlist.

## PostgreSQL backup process: S603

File:

```text
src/argus/storage/postgres_backup.py
```

`subprocess.run` запускает argv list с `shell=False`; executable — фиксированные PostgreSQL tools. Password передаётся через environment, не shell command.

Исключение не разрешает `shell=True` или arbitrary executable names.

## Secret posture status string: S105

File:

```text
src/argus/security/runtime_posture.py
```

`token_file_status = "pending_creation"` — diagnostic state label, а не secret value.

## Internal invariants: S101

Files:

```text
src/argus/history/timeline.py
src/argus/sources/document_web.py
src/argus/sources/json_feed.py
```

Assertions стоят после explicit validation и выражают internal impossible-state/type invariants. Authentication/authorization/input validation не должны зависеть от assert execution.

## Fallback transitions: S110/S112

Files:

```text
src/argus/sources/generic_web.py
src/argus/sources/recipe_web.py
src/argus/storage/postgres_migrations.py
```

Generic/SiteRecipe web path может намеренно перейти к следующей retrieval strategy при failure candidate recipe/replay URL. `UnsafeUrlError` не должен swallowing'иться.

Migration advisory-unlock cleanup может быть best-effort в `finally`, потому что закрытие PostgreSQL connection освобождает session advisory lock.

Исключения не разрешают молча поглощать SSRF/security/factual/storage failures.

## Review rule

При материальном изменении файла с security exception нужно заново проверить применимость rationale. Если исходная причина исчезла, исключение удаляется или сужается до merge.
