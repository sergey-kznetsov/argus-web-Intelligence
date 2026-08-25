# Security lint exceptions

This file documents the narrow Ruff `S`-rule exceptions used by ARGUS. The CI command `ruff check src --select S` remains mandatory; exceptions are per-file and must not be broadened without a new security review.

Verified: 2026-08-25.

## Dynamic SQL: S608

Affected files:

- `src/argus/result_delivery.py`
- `src/argus/storage/fenced_postgres.py`
- `src/argus/storage/postgres.py`
- `src/argus/storage/postgres_migrations.py`
- `src/argus/storage/postgres_operations.py`
- `src/argus/storage/postgres_storage_stats.py`
- `src/argus/storage/sqlite.py`

Reason: SQL values continue to use query parameters. The interpolated identifiers are schema/table/id-column names selected only from internal fixed allowlists or constants such as `observations`, `evidence`, `observation_id`, `evidence_id` and the constant `argus` schema. No request, URL, consumer, analysis or source text is permitted to become an SQL identifier.

This exception does not permit interpolating user-controlled SQL fragments. Any new dynamic identifier must have an explicit fixed allowlist before it may use this exception.

## PostgreSQL backup process: S603

Affected file:

- `src/argus/storage/postgres_backup.py`

Reason: `subprocess.run` executes an argv list directly with `shell=False`; commands are the fixed PostgreSQL tools used by the backup/restore helper. Database passwords are provided through the process environment and not concatenated into a shell command.

This exception does not permit `shell=True` or arbitrary user-supplied executable names.

## Secret posture status string: S105

Affected file:

- `src/argus/security/runtime_posture.py`

Reason: `token_file_status = "pending_creation"` is a diagnostic state label, not a password/token/secret value.

## Internal type/control-flow invariants: S101

Affected files:

- `src/argus/history/timeline.py`
- `src/argus/sources/document_web.py`
- `src/argus/sources/json_feed.py`

Reason: these assertions follow explicit branch/schema validation and express internal impossible-state/type invariants; they are not authentication, authorization or input validation controls. Product correctness must not depend on an assertion being executed.

They remain candidates for ordinary explicit invariant exceptions during code cleanup. The exception exists so the security gate does not mistake them for security controls.

## Deliberate fallback transitions: S110/S112

Affected files:

- `src/argus/sources/generic_web.py`
- `src/argus/sources/recipe_web.py`
- `src/argus/storage/postgres_migrations.py`

Reasons:

- Generic/SiteRecipe web code deliberately proceeds to the next retrieval strategy when a candidate recipe/replay URL fails. `UnsafeUrlError` is always re-raised and is never swallowed.
- The migration advisory-unlock cleanup is best-effort in `finally`; closing the PostgreSQL connection releases the session-level advisory lock even when explicit unlock fails.

These exceptions do not permit silent swallowing of SSRF/security errors or factual extraction/storage failures.

## Review rule

Whenever a security exception file is materially changed, the exception must be reviewed against the new code. If its original rationale no longer applies, remove or narrow the exception before merging.
