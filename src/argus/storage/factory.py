from __future__ import annotations

from argus.config import Settings
from argus.storage.atomic_sqlite import AtomicSQLiteRepository
from argus.storage.base import Repository
from argus.storage.fenced_postgres import FencedPostgresRepository


def build_repository(settings: Settings) -> Repository:
    if settings.storage_backend == "sqlite":
        return AtomicSQLiteRepository(settings.db_path)
    if settings.storage_backend == "postgresql":
        dsn = settings.database_dsn_value()
        if not dsn:
            raise RuntimeError(
                "ARGUS PostgreSQL storage requires a DSN from the deployment manager "
                "or ARGUS_DATABASE_DSN[_FILE]"
            )
        return FencedPostgresRepository(
            dsn,
            min_size=settings.postgres_pool_min_size,
            max_size=settings.postgres_pool_max_size,
            timeout_seconds=settings.postgres_pool_timeout_seconds,
            max_waiting=settings.postgres_pool_max_waiting,
        )
    raise RuntimeError(f"unsupported ARGUS storage backend: {settings.storage_backend}")