# PostgreSQL operations

ARGUS is a standalone infrastructure service and owns its PostgreSQL database lifecycle independently from Geo Analyzer TEST and PROD. The canonical server deployment uses the PostgreSQL instance reachable by ARGUS, but the service database and login are dedicated to ARGUS:

- database: `argus`;
- service role: `argus`;
- application schema: `argus`;
- DSN secret: `C:\ProgramData\ARGUS\secrets\database-dsn.txt`.

Geo Analyzer databases and environment files are consumer concerns and must never be used as the ARGUS database configuration source. In particular, the ARGUS deployment must not read or copy `GEOANALYZER_DATABASE_DSN`, `GEOANALYZER_DATABASE_DSN_FILE`, PROD `saas.env`, or TEST `saas.env`.

The PostgreSQL server itself may be shared operational infrastructure, but database ownership, credentials, backup/restore and lifecycle remain isolated. ARGUS database procedures must never drop, dump or restore unrelated Geo Analyzer databases or schemas.

## Deployment rule

ARGUS has one canonical standalone deployment. Geo Analyzer TEST and PROD are consumers of the same ARGUS service and do not create separate ARGUS instances.

`deploy/windows/deploy-server.ps1` requires an existing ARGUS-owned DSN file. It validates that the DSN targets `database=argus` with service role `user=argus` and refuses deployment otherwise. The deployment script never provisions the PostgreSQL administrator account and never derives the ARGUS DSN from a Geo Analyzer environment file.

The GitHub credential used for deployment is also ARGUS-owned. When authentication is required, provide either process-local `ARGUS_GITHUB_TOKEN` or `C:\ProgramData\ARGUS\secrets\github-token.txt`. Do not reuse Geo Analyzer environment files as a deployment dependency.

Database-changing procedures are validated through the isolated ARGUS storage contract before consumer E2E testing. Consumer changes are validated in Geo Analyzer TEST before being connected or promoted in PROD.

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

ARGUS backup is schema-scoped inside the dedicated `argus` database and uses PostgreSQL custom archive format:

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
2. requires the archive schema version to match the running ARGUS schema version exactly;
3. calls `pg_restore` with `--single-transaction --clean --if-exists --schema=argus`;
4. restores without ownership/privilege commands;
5. runs normal migration verification after restore;
6. verifies the resulting schema version.

Exact schema-version matching is intentional. A custom archive from an older schema does not know about objects introduced by newer migrations; selectively cleaning such an archive over a newer live schema can leave dependency conflicts or mixed-version objects. To restore an older backup, run the matching ARGUS version, restore and verify it there, then update ARGUS through the normal migration/update path.

`--single-transaction` is intentional: PostgreSQL must either apply the whole restore or leave the database unchanged by that restore attempt.

Before restoring the canonical server database, stop the standalone `ARGUS-API` and `ARGUS-Worker` scheduled tasks so no collection writes occur while the schema is being replaced. Restore and verify into an isolated recovery database first whenever practical, then run `check`, API readiness and at least one end-to-end consumer collection before considering the recovery complete.

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

## JSONB and relation growth

Inspect actual storage growth without loading stored JSONB into ARGUS memory:

```bash
python -m argus.storage.cli storage-stats
```

The command uses PostgreSQL-native size functions and reports:

- row count for each JSONB-bearing ARGUS table;
- sum, average and maximum `pg_column_size(body)`;
- table bytes, index bytes and total relation bytes including TOAST where PostgreSQL accounts for it;
- the table holding the largest aggregate JSONB volume;
- the table containing the largest individual JSONB row.

The audited JSONB tables are `collections`, `observations`, `evidence`, `snapshots` and `site_recipes`. Relation sizes also include leases, worker registrations, idempotency, result-access markers and migration metadata.

A large result is not automatically truncated at the database layer because doing so could silently corrupt factual Evidence. Growth is controlled first by crawler/extractor/result limits and retention, then observed with these database metrics. Unexpected increases in maximum row size or total relation bytes are investigated before raising limits.

## Operational inspection

```bash
python -m argus.storage.cli operations
python -m argus.storage.cli storage-stats
```

`operations` reports queue/worker/lease state and PostgreSQL pool statistics. `storage-stats` reports JSONB and physical relation growth. Neither command reads complete CollectionResult payloads into Python memory.

For incident recovery, preserve the failing archive, manifest, ARGUS version, schema version and relevant secret-safe logs. Do not edit a backup manifest to force a mismatched archive through verification.
