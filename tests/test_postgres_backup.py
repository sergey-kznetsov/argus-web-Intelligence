from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.storage import postgres_backup


def test_command_context_keeps_password_out_of_process_arguments():
    safe_conninfo, environment = postgres_backup._command_context(
        "postgresql://argus:super-secret@db.example:5432/geo?sslmode=require"
    )

    assert "super-secret" not in safe_conninfo
    assert "password" not in safe_conninfo.casefold()
    assert environment["PGPASSWORD"] == "super-secret"
    assert "dbname=geo" in safe_conninfo
    assert "sslmode=require" in safe_conninfo


def test_backup_is_atomic_hash_backed_and_password_safe(tmp_path: Path, monkeypatch):
    archive = tmp_path / "argus.dump"
    calls: list[tuple[list[str], dict[str, str]]] = []

    def fake_run(command, *, env, stdout, stderr, text, check):
        del stdout, stderr, text, check
        calls.append((list(command), dict(env)))
        output_path = Path(command[command.index("--file") + 1])
        output_path.write_bytes(b"trusted-argus-backup")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(postgres_backup.subprocess, "run", fake_run)
    manifest = postgres_backup.backup_argus_schema(
        "postgresql://argus:super-secret@localhost:5432/geo",
        archive,
        schema_version=6,
    )

    assert archive.read_bytes() == b"trusted-argus-backup"
    assert manifest.schema == "argus"
    assert manifest.schema_version == 6
    assert manifest.archive_bytes == len(b"trusted-argus-backup")
    sidecar = postgres_backup.manifest_path(archive)
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert payload["archive_sha256"] == manifest.archive_sha256

    command, environment = calls[0]
    assert command[0] == "pg_dump"
    assert "--format=custom" in command
    assert "--schema=argus" in command
    assert "--no-owner" in command
    assert "--no-privileges" in command
    assert all("super-secret" not in item for item in command)
    assert environment["PGPASSWORD"] == "super-secret"


def test_backup_refuses_to_overwrite_by_default(tmp_path: Path):
    archive = tmp_path / "argus.dump"
    archive.write_bytes(b"existing")

    with pytest.raises(FileExistsError):
        postgres_backup.backup_argus_schema(
            "postgresql://argus@localhost/geo",
            archive,
            schema_version=6,
        )


def test_verify_rejects_tampered_archive(tmp_path: Path, monkeypatch):
    archive = tmp_path / "argus.dump"

    def fake_run(command, *, env, stdout, stderr, text, check):
        del env, stdout, stderr, text, check
        Path(command[command.index("--file") + 1]).write_bytes(b"original")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(postgres_backup.subprocess, "run", fake_run)
    postgres_backup.backup_argus_schema(
        "postgresql://argus@localhost/geo",
        archive,
        schema_version=6,
    )
    archive.write_bytes(b"tampered")

    with pytest.raises(RuntimeError, match="integrity check failed"):
        postgres_backup.verify_argus_backup(archive)


def test_restore_requires_explicit_destructive_confirmation(tmp_path: Path):
    with pytest.raises(ValueError, match="replace_existing_argus"):
        postgres_backup.restore_argus_schema(
            "postgresql://argus@localhost/geo",
            tmp_path / "argus.dump",
            replace_existing_argus=False,
        )


def test_restore_uses_verified_archive_and_password_safe_command(tmp_path: Path, monkeypatch):
    archive = tmp_path / "argus.dump"
    calls: list[tuple[list[str], dict[str, str]]] = []

    def fake_run(command, *, env, stdout, stderr, text, check):
        del stdout, stderr, text, check
        calls.append((list(command), dict(env)))
        if command[0] == "pg_dump":
            Path(command[command.index("--file") + 1]).write_bytes(b"backup")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(postgres_backup.subprocess, "run", fake_run)
    dsn = "postgresql://argus:super-secret@localhost:5432/geo"
    postgres_backup.backup_argus_schema(dsn, archive, schema_version=6)
    manifest = postgres_backup.restore_argus_schema(
        dsn,
        archive,
        replace_existing_argus=True,
    )

    assert manifest.schema_version == 6
    restore_command, restore_environment = calls[-1]
    assert restore_command[0] == "pg_restore"
    assert "--exit-on-error" in restore_command
    assert "--clean" in restore_command
    assert "--if-exists" in restore_command
    assert "--schema=argus" in restore_command
    assert all("super-secret" not in item for item in restore_command)
    assert restore_environment["PGPASSWORD"] == "super-secret"


def test_postgres_tool_failure_is_bounded_and_redacted(monkeypatch):
    def failed_run(command, *, env, stdout, stderr, text, check):
        del command, env, stdout, stderr, text, check
        return SimpleNamespace(
            returncode=2,
            stdout="",
            stderr="connection failed password=super-secret",
        )

    monkeypatch.setattr(postgres_backup.subprocess, "run", failed_run)

    with pytest.raises(RuntimeError) as error:
        postgres_backup._run_tool(["pg_dump"], {"PGPASSWORD": "super-secret"})

    assert "super-secret" not in str(error.value)
