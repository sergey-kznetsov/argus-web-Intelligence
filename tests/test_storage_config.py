from pathlib import Path

import pytest

from argus.config import Settings
from argus.storage.atomic_sqlite import AtomicSQLiteRepository
from argus.storage.factory import build_repository
from argus.storage.fenced_postgres import FencedPostgresRepository
from argus.storage.postgres import PostgresRepository
from argus.storage.sqlite import SQLiteRepository


def test_geo_analyzer_database_secret_file_has_priority(tmp_path: Path, monkeypatch):
    secret_file = tmp_path / "database-dsn.txt"
    secret_file.write_text(
        "postgresql://file-user:file-password@db.example/argus",
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "GEOANALYZER_DATABASE_DSN",
        "postgresql://env-user:env-password@db.example/argus",
    )
    monkeypatch.setenv("GEOANALYZER_DATABASE_DSN_FILE", str(secret_file))

    settings = Settings(storage_backend="postgresql")

    assert settings.database_dsn_value() == (
        "postgresql://file-user:file-password@db.example/argus"
    )
    assert "file-password" not in repr(settings)
    assert "env-password" not in repr(settings)


def test_storage_factory_uses_atomic_sqlite_for_local_backend(tmp_path: Path):
    settings = Settings(
        storage_backend="sqlite",
        db_path=tmp_path / "argus.sqlite",
        token_file=tmp_path / "token",
    )
    repository = build_repository(settings)
    assert isinstance(repository, AtomicSQLiteRepository)
    assert isinstance(repository, SQLiteRepository)


def test_storage_factory_builds_fenced_postgres_without_opening_network(monkeypatch):
    monkeypatch.setenv(
        "GEOANALYZER_DATABASE_DSN",
        "postgresql://argus:secret@127.0.0.1:5432/argus",
    )
    settings = Settings(
        storage_backend="postgresql",
        postgres_pool_max_waiting=7,
    )
    repository = build_repository(settings)
    assert isinstance(repository, FencedPostgresRepository)
    assert isinstance(repository, PostgresRepository)
    assert repository.pool_stats()["max_waiting"] == 7


def test_postgres_pool_wait_queue_must_be_bounded_positive():
    with pytest.raises(ValueError):
        Settings(postgres_pool_max_waiting=0)


def test_postgres_storage_requires_database_secret(monkeypatch):
    for name in (
        "ARGUS_DATABASE_DSN",
        "ARGUS_DATABASE_DSN_FILE",
        "GEOANALYZER_DATABASE_DSN",
        "GEOANALYZER_DATABASE_DSN_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(storage_backend="postgresql")
    with pytest.raises(RuntimeError, match="requires a DSN"):
        build_repository(settings)
