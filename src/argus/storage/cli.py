from __future__ import annotations

import argparse
import asyncio
import json

from argus.config import get_settings
from argus.storage.postgres_migrations import (
    EXPECTED_SCHEMA_VERSION,
    current_postgres_schema_version,
    run_postgres_migrations,
)


def _dsn() -> str:
    settings = get_settings()
    value = settings.database_dsn_value()
    if not value:
        raise SystemExit(
            "PostgreSQL DSN is required through ARGUS_DATABASE_DSN, "
            "ARGUS_DATABASE_DSN_FILE, GEOANALYZER_DATABASE_DSN or "
            "GEOANALYZER_DATABASE_DSN_FILE"
        )
    return value


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


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m argus.storage.cli")
    parser.add_argument("command", choices=("migrate", "check"))
    args = parser.parse_args()
    if args.command == "migrate":
        asyncio.run(_migrate())
    else:
        asyncio.run(_check())


if __name__ == "__main__":
    main()
