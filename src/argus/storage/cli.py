from __future__ import annotations

import argparse
import asyncio
import json

from argus.config import Settings, get_settings
from argus.storage.postgres import PostgresRepository
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


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m argus.storage.cli")
    parser.add_argument("command", choices=("migrate", "check", "operations", "retention"))
    args = parser.parse_args()
    commands = {
        "migrate": _migrate,
        "check": _check,
        "operations": _operations,
        "retention": _retention,
    }
    asyncio.run(commands[args.command]())


if __name__ == "__main__":
    main()
