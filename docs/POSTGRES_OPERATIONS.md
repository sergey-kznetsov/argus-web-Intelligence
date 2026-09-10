# Операции PostgreSQL ARGUS

ARGUS — standalone infrastructure service с отдельным database lifecycle от Geo Analyzer TEST/PROD.

Канонический server deployment использует:

```text
database:     argus
service role: argus
schema:       argus
DSN secret:   C:\ProgramData\ARGUS\secrets\database-dsn.txt
```

Физический PostgreSQL server может быть общим инфраструктурным сервером, но database, login, backup/restore и lifecycle ARGUS изолированы.

Geo Analyzer databases и environment files не являются источником ARGUS DB config. Deploy не должен читать или копировать `GEOANALYZER_DATABASE_DSN`, TEST/PROD `saas.env`.

## Deployment rule

`deploy/windows/deploy-server.ps1` требует существующий ARGUS-owned DSN file и проверяет:

```text
database=argus
user=argus
```

При другом database/user deployment прекращается. Скрипт не создаёт PostgreSQL administrator и не выводит credentials.

GitHub auth при необходимости берётся из process-local `ARGUS_GITHUB_TOKEN` или `C:\ProgramData\ARGUS\secrets\github-token.txt`.

Expected schema version задаёт `argus.storage.postgres_migrations.EXPECTED_SCHEMA_VERSION`. API/worker отказываются от readiness при несовпадающей схеме.

## Migrations

```bash
python -m argus.storage.cli migrate
python -m argus.storage.cli check
```

Migrations versioned, checksum-protected и выполняются под PostgreSQL advisory lock. Каждая version применяется одной transaction.

Уже записанные migration versions считаются immutable. Изменение name/SQL меняет checksum и вызывает verification failure вместо тихого принятия неизвестного состояния.

## Backup

ARGUS backup ограничен schema `argus` внутри dedicated database и использует PostgreSQL custom archive format:

```bash
python -m argus.storage.cli backup --output /secure/path/argus.dump
```

Existing archive не перезаписывается без `--force`.

Backup command:

- проверяет текущую schema version;
- вызывает `pg_dump --format=custom --schema=argus`;
- исключает ownership/privilege restoration;
- пишет temporary file и atomically rename после success;
- создаёт sidecar `<archive>.argus-backup.json`;
- фиксирует SHA-256, size, ARGUS version, schema version;
- передаёт PostgreSQL password через child-process environment, а не command line.

Manifest — integrity check, не cryptographic signature. Нельзя восстанавливать недоверенный dump.

Проверка:

```bash
python -m argus.storage.cli verify-backup --input /secure/path/argus.dump
```

## Restore

Restore разрушителен для existing schema `argus` и требует явного flag:

```bash
python -m argus.storage.cli restore \
  --input /secure/path/argus.dump \
  --replace-existing-argus
```

Последовательность:

1. verify manifest/size/SHA-256;
2. потребовать exact match archive schema version и running ARGUS schema version;
3. выполнить `pg_restore --single-transaction --clean --if-exists --schema=argus`;
4. не восстанавливать ownership/privileges;
5. выполнить normal migration verification;
6. проверить final schema version.

Для older backup нужен matching ARGUS version: восстановить/проверить в нём, затем обновлять обычными migrations. Нельзя накладывать old archive на newer schema и получать mixed state.

Перед restore canonical server database нужно остановить `ARGUS-API` и `ARGUS-Worker`, чтобы не было collection writes. По возможности сначала восстановить в isolated recovery database и провести check + API readiness + consumer collection.

## Connection pool

Defaults:

```text
ARGUS_POSTGRES_POOL_MIN_SIZE=1
ARGUS_POSTGRES_POOL_MAX_SIZE=8
ARGUS_POSTGRES_POOL_TIMEOUT_SECONDS=30
ARGUS_POSTGRES_POOL_MAX_WAITING=32
```

`max_waiting` ограничивает число coroutines в ожидании connection. При saturation Psycopg возвращает controlled pool error, а не бесконечно накапливает очередь.

Repository health и `python -m argus.storage.cli operations` показывают pool size/availability, waiting, queue/error counters и cumulative wait time. Увеличивать pool нужно только после load testing.

## Result-read retention grace

Retention никогда не удаляет active `queued`/`running` collections. Terminal collection также временно защищена, пока consumer читает result.

Каждый PostgreSQL result read обновляет:

```text
argus.collection_result_access.last_accessed_at
```

Default grace:

```text
ARGUS_RETENTION_RESULT_ACCESS_GRACE_SECONDS=3600
```

Успешная следующая page refresh'ит grace. Marker отделён от `collections.updated_at`, поэтому чтение result не имитирует изменение анализа.

## Retention

Manual:

```bash
python -m argus.storage.cli retention
```

Automatic workers выполняют bounded passes под одним advisory lock.

Правила:

- active collections не удаляются;
- recently-read terminal collections защищены grace period;
- старые terminal collections удаляются bounded batches;
- child rows удаляются по FK semantics;
- stale idempotency mappings и worker registrations очищаются bounded;
- old snapshots удаляются bounded, но newest snapshot каждого `source_url` сохраняется как diff baseline;
- SiteRecipe records автоматически не purge'ятся обычной collection retention.

## Storage growth

```bash
python -m argus.storage.cli storage-stats
```

Команда использует PostgreSQL-native size functions без загрузки полного JSONB в Python и показывает:

- row count JSONB-bearing tables;
- sum/avg/max `pg_column_size(body)`;
- table/index/total relation bytes;
- largest aggregate JSONB table;
- largest individual JSONB row.

Audited JSONB tables:

```text
collections
observations
evidence
snapshots
site_recipes
```

Большой result не обрезается молча на database layer: рост контролируется extraction/result limits + retention и наблюдается этими метриками.

## Operational inspection

```bash
python -m argus.storage.cli operations
python -m argus.storage.cli storage-stats
```

Операции ARGUS не должны drop/dump/restore unrelated Geo Analyzer databases/schemas. Для incident recovery сохраняются failing archive, manifest, ARGUS/schema versions и secret-safe logs; manifest нельзя редактировать для обхода verification.
