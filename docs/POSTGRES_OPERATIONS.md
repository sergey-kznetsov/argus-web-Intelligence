# PostgreSQL operations

ARGUS uses the shared Geo Analyzer PostgreSQL instance but owns only the `argus` schema. Operational procedures must therefore remain schema-scoped and must never drop, dump or restore unrelated Geo Analyzer schemas.

## Deployment rule

Database-changing procedures are validated in TEST before PROD. A production update must not use manual ad-hoc SQL when the same change can be represented by an ARGUS migration or the universal Geo Analyzer module update flow.

The expected schema version is defined by `argus.storage.postgres_migrations.EXPECTED_SCHEMA_VERSION`. API and worker startup reject a database that has not been migrated to that version.

## Migrations

Apply and verify migrations with:

```bash
python -m argus.storage.cli migrate
python -m argus.storage.cli check
```

Migrations are versioned, checksum-protected and run under a PostgreSQL advisory lock. Each version executes inside one transaction. A failed migration must therefore leave neither its schema changes nor its migration record committed.

Existing migration versions are immutable. Changing the name or SQL of an already recorded version changes its checksum and causes startup/migration verification to fail instead of silently trusting an unknown schema.

## Backup

ARGUS backup is schema-scoped and uses PostgreSQL custom archive format:

```bash
python -m argus.storage.cli backup --output /secure/path/argus.dump
```

Existing archives are not overwritten unless the operator explicitly adds `--force`.

The backup command:

- refuses to dump an ARGUS schema whose version does not match the running ARGUS version;
- invokes `pg_dump --format=custom --schema=argus`;
- does not include ownership or privilege restoration;
- writes to a temporary file in the destination directory and atomically renames it after success;
- writes a sidecar `<archive>.argus-backup.json` manifest;
- records archive SHA-256, size, ARGUS version and schema version;
- removes the PostgreSQL password from process arguments and supplies it only through the child-process environment.

The manifest is an integrity check, not a cryptographic authenticity signature. Only backups created and stored through a trusted ARGUS operational path should be restored. PostgreSQL dumps can contain executable SQL objects; never restore an untrusted dump.

Verify an archive before moving or restoring it:

```bash
python -m argus.storage.cli verify-backup --input /secure/path/argus.dump
```

## Restore

Restore is destructive for the existing `argus` schema and requires an explicit flag:

```bash
python -m argus.storage.cli restore \
  --input /secure/path/argus.dump \
  --replace-existing-argus
```

The restore path:

1. verifies the manifest, archive size and SHA-256;
2. refuses an archive whose schema version is newer than the running ARGUS runtime;
3. calls `pg_restore` with `--single-transaction --clean --if-exists --schema=argus`;
4. restores without ownership/privilege commands;
5. runs normal ARGUS migrations after restore so an older compatible backup reaches the current schema version;
6. verifies the resulting schema version.

`--single-transaction` is intentional: PostgreSQL must either apply the whole restore or leave the database unchanged by that restore attempt.

Before PROD restore, stop or disable ARGUS API/worker processes in the module manager so no collection writes occur while the schema is being replaced. Perform the restore in TEST first using a copy of the intended archive, then run `check`, API readiness and at least one end-to-end collection.

## Connection-pool saturation

ARGUS bounds both pool size and the number of requests allowed to wait for a connection:

```bash
ARGUS_POSTGRES_POOL_MIN_SIZE=1
ARGUS_POSTGRES_POOL_MAX_SIZE=8
ARGUS_POSTGRES_POOL_TIMEOUT_SECONDS=30
ARGUS_POSTGRES_POOL_MAX_WAITING=32
```

`max_waiting` prevents an overloaded process from accumulating an unbounded coroutine queue behind a saturated PostgreSQL pool. When the waiting limit is reached, Psycopg raises a controlled pool error instead of accepting additional waiters.

Repository health and `python -m argus.storage.cli operations` expose bounded pool statistics including pool size/availability, waiting requests, queue/error counters and cumulative wait time. Tune pool size only after load testing; increasing connections blindly can move saturation from ARGUS into PostgreSQL.

## Result-read retention grace

Retention never deletes `queued` or `running` collections. Terminal collections are also protected while a consumer is actively retrieving a result.

Every PostgreSQL result read (`summary`, bounded full result, Observation page or Evidence page) updates `argus.collection_result_access.last_accessed_at`. Retention skips a terminal collection whose latest result access is inside:

```bash
ARGUS_RETENTION_RESULT_ACCESS_GRACE_SECONDS=3600
```

Each successful page request refreshes the grace period. The marker is stored separately from `collections.updated_at`, so reading a result does not pretend that the analysis itself changed. The access row has `ON DELETE CASCADE` and disappears with its collection.

The grace period is not a permanent retention exemption. A consumer that stops reading eventually allows the normal collection-retention policy to apply.

## Retention

Manual pass:

```bash
python -m argus.storage.cli retention
```

Automatic passes run from workers under one PostgreSQL advisory lock. Current rules:

- active collections are never deleted;
- recently read terminal collections are protected by result-access grace;
- terminal collections older than collection retention are deleted in bounded batches;
- collection child rows follow foreign-key cleanup;
- stale idempotency mappings and worker registrations are bounded-cleaned;
- old snapshots are cleaned in bounded batches, while the newest snapshot for each source URL remains available as the next diff baseline.

## Operational inspection

```bash
python -m argus.storage.cli operations
```

The command reports queue/worker/lease state and PostgreSQL pool statistics without reading complete CollectionResult payloads into memory.

For incident recovery, preserve the failing archive, manifest, ARGUS version, schema version and relevant secret-safe logs. Do not edit a backup manifest to force a mismatched archive through verification.
