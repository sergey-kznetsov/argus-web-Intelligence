from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Mapping

from psycopg.conninfo import conninfo_to_dict, make_conninfo

from argus import __version__
from argus.security.redaction import redact_text

_ARGUS_SCHEMA = "argus"
_BACKUP_FORMAT = "custom"
_MANIFEST_SUFFIX = ".argus-backup.json"


@dataclass(frozen=True, slots=True)
class PostgresBackupManifest:
    schema: str
    schema_version: int
    argus_version: str
    format: str
    created_at: str
    archive_sha256: str
    archive_bytes: int

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "PostgresBackupManifest":
        return cls(
            schema=str(value.get("schema", "")),
            schema_version=int(value.get("schema_version", 0)),
            argus_version=str(value.get("argus_version", "")),
            format=str(value.get("format", "")),
            created_at=str(value.get("created_at", "")),
            archive_sha256=str(value.get("archive_sha256", "")),
            archive_bytes=int(value.get("archive_bytes", 0)),
        )


def manifest_path(archive_path: Path) -> Path:
    return archive_path.with_name(archive_path.name + _MANIFEST_SUFFIX)


def _command_context(dsn: str) -> tuple[str, dict[str, str]]:
    """Return password-free libpq conninfo plus a subprocess-only password environment."""

    params = conninfo_to_dict(dsn)
    password = params.pop("password", None)
    safe_conninfo = make_conninfo(**params)
    environment = dict(os.environ)
    environment.pop("PGPASSWORD", None)
    if password:
        environment["PGPASSWORD"] = password
    environment["PGAPPNAME"] = "argus-postgres-operations"
    return safe_conninfo, environment


def _run_tool(command: list[str], environment: Mapping[str, str]) -> None:
    try:
        completed = subprocess.run(
            command,
            env=dict(environment),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"PostgreSQL client tool '{command[0]}' is not installed or not on PATH"
        ) from exc
    if completed.returncode != 0:
        diagnostic = redact_text(completed.stderr or completed.stdout or "", max_length=2000)
        raise RuntimeError(
            f"PostgreSQL client tool '{command[0]}' failed with exit code "
            f"{completed.returncode}: {diagnostic}"
        )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _restrict_permissions(path: Path) -> None:
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def backup_argus_schema(
    dsn: str,
    archive_path: Path,
    *,
    schema_version: int,
    overwrite: bool = False,
    pg_dump_binary: str = "pg_dump",
) -> PostgresBackupManifest:
    """Create one atomic custom-format backup of only the ARGUS PostgreSQL schema."""

    target = archive_path.expanduser().resolve()
    if target.exists() and not overwrite:
        raise FileExistsError(f"backup archive already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    safe_conninfo, environment = _command_context(dsn)

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        _restrict_permissions(temporary)
        command = [
            pg_dump_binary,
            "--format=custom",
            f"--schema={_ARGUS_SCHEMA}",
            "--no-owner",
            "--no-privileges",
            "--file",
            str(temporary),
            "--dbname",
            safe_conninfo,
        ]
        _run_tool(command, environment)
        if not temporary.is_file() or temporary.stat().st_size <= 0:
            raise RuntimeError("pg_dump completed without producing a non-empty archive")
        archive_sha256 = _sha256_file(temporary)
        archive_bytes = temporary.stat().st_size
        os.replace(temporary, target)
        _restrict_permissions(target)
    finally:
        if temporary.exists():
            temporary.unlink(missing_ok=True)

    manifest = PostgresBackupManifest(
        schema=_ARGUS_SCHEMA,
        schema_version=int(schema_version),
        argus_version=__version__,
        format=_BACKUP_FORMAT,
        created_at=datetime.now(UTC).isoformat(),
        archive_sha256=archive_sha256,
        archive_bytes=archive_bytes,
    )
    sidecar = manifest_path(target)
    sidecar.write_text(
        json.dumps(asdict(manifest), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    _restrict_permissions(sidecar)
    return manifest


def verify_argus_backup(archive_path: Path) -> PostgresBackupManifest:
    target = archive_path.expanduser().resolve()
    sidecar = manifest_path(target)
    if not target.is_file():
        raise FileNotFoundError(f"backup archive does not exist: {target}")
    if not sidecar.is_file():
        raise FileNotFoundError(f"backup manifest does not exist: {sidecar}")
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
        manifest = PostgresBackupManifest.from_mapping(payload)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("backup manifest is invalid") from exc
    if manifest.schema != _ARGUS_SCHEMA or manifest.format != _BACKUP_FORMAT:
        raise RuntimeError("backup manifest is not an ARGUS custom-format schema backup")
    actual_bytes = target.stat().st_size
    actual_hash = _sha256_file(target)
    if actual_bytes != manifest.archive_bytes or actual_hash != manifest.archive_sha256:
        raise RuntimeError("backup archive integrity check failed")
    return manifest


def restore_argus_schema(
    dsn: str,
    archive_path: Path,
    *,
    replace_existing_argus: bool,
    pg_restore_binary: str = "pg_restore",
) -> PostgresBackupManifest:
    """Restore a verified ARGUS archive; destructive replacement requires an explicit flag."""

    if not replace_existing_argus:
        raise ValueError("restore requires replace_existing_argus=True")
    target = archive_path.expanduser().resolve()
    manifest = verify_argus_backup(target)
    safe_conninfo, environment = _command_context(dsn)
    command = [
        pg_restore_binary,
        "--exit-on-error",
        "--clean",
        "--if-exists",
        f"--schema={_ARGUS_SCHEMA}",
        "--no-owner",
        "--no-privileges",
        "--dbname",
        safe_conninfo,
        str(target),
    ]
    _run_tool(command, environment)
    return manifest
