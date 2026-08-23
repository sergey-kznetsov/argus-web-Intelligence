from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from argus.config import Settings, get_settings
from argus.storage.postgres import PostgresRepository
from argus.storage.postgres_backup import (
    backup_argus_schema,
    restore_argus_schema,
    verify_argus_backup,
)
from argus.storage.postgres_migrations import (
    EXPECTED_SCHEMA_VERSION,
    current_postgres_schema_version,
    run_postgres_migrations,
)


def _settings_and_dsn() -> tuple[Settings, str]:
    settings = get_settings()
    value = settings.database_dsn_value()
    if not value:
        raise SystemExit(
            "PostgreSQL DSN is required through ARGUS_DATABASE_DSN, "
            "ARGUS_DATABASE_DSN_FILE, GEOANALYZER_DATABASE_DSN or "
            "GEOANALYZER_DATABASE_DSN_FILE"
        )
    return settings, value


def _dsn() -> str:
    return _settings_and_dsn()[1]


def _repository(settings: Settings, dsn: str) -> PostgresRepository:
    return PostgresRepository(
        dsn,
        min_size=settings.postgres_pool_min_size,
        max_size=settings.postgres_pool_max_size,
        timeout_seconds=settings.postgres_pool_timeout_seconds,
        max_waiting=settings.postgres_pool_max_waiting,
    )


async def _migrate() -> None:
    dsn = _dsn()
    applied = await run_postgres_migrations(dsn)
    version = await current_postgres_schema_version(dsn)
    print(
        json.dumps(
            {
                "status": "ok",
                "schema": "argus",
                "schema_version": version,
                "expected_schema_version": EXPECTED_SCHEMA_VERSION,
                "applied": applied,
            },
            ensure_ascii=False,
        )
    )


async def _check() -> None:
    version = await current_postgres_schema_version(_dsn())
    if version != EXPECTED_SCHEMA_VERSION:
        raise SystemExit(
            f"ARGUS PostgreSQL schema version {version}; expected {EXPECTED_SCHEMA_VERSION}"
        )
    print(
        json.dumps(
            {
                "status": "ok",
                "schema": "argus",
                "schema_version": version,
            },
            ensure_ascii=False,
        )
    )


async def _operations() -> None:
    settings, dsn = _settings_and_dsn()
    repository = _repository(settings, dsn)
    await repository.initialize()
    try:
        metrics = await repository.queue_metrics(
            worker_max_age_seconds=settings.worker_health_max_age_seconds
        )
        payload = metrics.as_dict()
        payload.update(
            {
                "status": "ok",
                "schema": "argus",
                "max_active_collections": settings.queue_max_active_collections,
                "max_active_per_consumer": settings.queue_max_active_per_consumer,
                "idempotency_window_seconds": settings.idempotency_window_seconds,
                "postgres_pool": repository.pool_stats(),
            }
        )
        print(json.dumps(payload, ensure_ascii=False))
    finally:
        await repository.close()


async def _retention() -> None:
    settings, dsn = _settings_and_dsn()
    repository = _repository(settings, dsn)
    await repository.initialize()
    try:
        result = await repository.run_retention(
            idempotency_window_seconds=settings.idempotency_window_seconds,
            collection_retention_days=settings.retention_collection_days,
            snapshot_retention_days=settings.retention_snapshot_days,
            worker_registration_retention_days=(
                settings.retention_worker_registration_days
            ),
            batch_size=settings.retention_batch_size,
        )
        print(
            json.dumps(
                {
                    "status": "ok",
                    "schema": "argus",
                    **result.as_dict(),
                },
                ensure_ascii=False,
            )
        )
    finally:
        await repository.close()


async def _backup(output: Path, *, force: bool) -> None:
    _, dsn = _settings_and_dsn()
    version = await current_postgres_schema_version(dsn)
    if version != EXPECTED_SCHEMA_VERSION:
        raise SystemExit(
            f"refusing backup of ARGUS schema version {version}; "
            f"expected {EXPECTED_SCHEMA_VERSION}"
        )
    manifest = await asyncio.to_thread(
        backup_argus_schema,
        dsn,
        output,
        schema_version=version,
        overwrite=force,
    )
    print(
        json.dumps(
            {
                "status": "ok",
                "schema": manifest.schema,
                "schema_version": manifest.schema_version,
                "archive": str(output.expanduser().resolve()),
                "archive_sha256": manifest.archive_sha256,
                "archive_bytes": manifest.archive_bytes,
            },
            ensure_ascii=False,
        )
    )


async def _verify_backup(archive: Path) -> None:
    manifest = await asyncio.to_thread(verify_argus_backup, archive)
    print(
        json.dumps(
            {
                "status": "ok",
                "schema": manifest.schema,
                "schema_version": manifest.schema_version,
                "archive": str(archive.expanduser().resolve()),
                "archive_sha256": manifest.archive_sha256,
                "archive_bytes": manifest.archive_bytes,
            },
            ensure_ascii=False,
        )
    )


async def _restore(archive: Path, *, replace_existing_argus: bool) -> None:
    if not replace_existing_argus:
        raise SystemExit(
            "restore is destructive; pass --replace-existing-argus after verifying the target DB"
        )
    _, dsn = _settings_and_dsn()
    manifest = await asyncio.to_thread(verify_argus_backup, archive)
    if manifest.schema_version > EXPECTED_SCHEMA_VERSION:
        raise SystemExit(
            f"backup schema version {manifest.schema_version} is newer than this ARGUS "
            f"runtime ({EXPECTED_SCHEMA_VERSION})"
        )
    await asyncio.to_thread(
        restore_argus_schema,
        dsn,
        archive,
        replace_existing_argus=True,
    )
    applied = await run_postgres_migrations(dsn)
    version = await current_postgres_schema_version(dsn)
    if version != EXPECTED_SCHEMA_VERSION:
        raise SystemExit(
            f"restored ARGUS PostgreSQL schema version {version}; expected "
            f"{EXPECTED_SCHEMA_VERSION}"
        )
    print(
        json.dumps(
            {
                "status": "ok",
                "schema": "argus",
                "restored_from_schema_version": manifest.schema_version,
                "schema_version": version,
                "applied_migrations": applied,
                "archive": str(archive.expanduser().resolve()),
            },
            ensure_ascii=False,
        )
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m argus.storage.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate")
    commands.add_parser("check")
    commands.add_parser("operations")
    commands.add_parser("retention")

    backup = commands.add_parser("backup")
    backup.add_argument("--output", type=Path, required=True)
    backup.add_argument("--force", action="store_true")

    verify = commands.add_parser("verify-backup")
    verify.add_argument("--input", type=Path, required=True)

    restore = commands.add_parser("restore")
    restore.add_argument("--input", type=Path, required=True)
    restore.add_argument("--replace-existing-argus", action="store_true")
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.command == "migrate":
        asyncio.run(_migrate())
    elif args.command == "check":
        asyncio.run(_check())
    elif args.command == "operations":
        asyncio.run(_operations())
    elif args.command == "retention":
        asyncio.run(_retention())
    elif args.command == "backup":
        asyncio.run(_backup(args.output, force=args.force))
    elif args.command == "verify-backup":
        asyncio.run(_verify_backup(args.input))
    elif args.command == "restore":
        asyncio.run(
            _restore(
                args.input,
                replace_existing_argus=args.replace_existing_argus,
            )
        )
    else:
        raise SystemExit(f"unsupported command: {args.command}")


if __name__ == "__main__":
    main()