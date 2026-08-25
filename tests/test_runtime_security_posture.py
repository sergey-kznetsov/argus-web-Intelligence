from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from argus.bootstrap import build_services
from argus.config import Settings
from argus.security.auth import ensure_token
from argus.security.runtime_posture import enforce_runtime_security


def test_build_services_refuses_non_loopback_bind(tmp_path: Path):
    settings = Settings(
        host="0.0.0.0",
        db_path=tmp_path / "db.sqlite",
        token_file=tmp_path / "token",
    )

    with pytest.raises(RuntimeError, match="loopback"):
        build_services(settings)


def test_runtime_security_accepts_loopback_hosts(tmp_path: Path):
    for host in ("127.0.0.1", "localhost", "::1"):
        settings = Settings(
            host=host,
            db_path=tmp_path / f"{host.replace(':', '_')}.sqlite",
            token_file=tmp_path / f"token-{host.replace(':', '_')}",
        )
        posture = enforce_runtime_security(settings)
        assert posture["loopback_bind_enforced"] is True
        assert posture["configured_host_is_loopback"] is True
        assert posture["os_process_sandbox"] == "deployment_required"
        assert posture["network_egress_firewall"] == "deployment_required"


def test_missing_required_database_secret_file_fails_closed(tmp_path: Path):
    settings = Settings(
        database_dsn_file=tmp_path / "missing-dsn",
        db_path=tmp_path / "db.sqlite",
        token_file=tmp_path / "token",
    )

    with pytest.raises(RuntimeError, match="secret file"):
        enforce_runtime_security(settings)


@pytest.mark.skipif(os.name != "posix", reason="POSIX file mode test")
def test_runtime_security_hardens_database_secret_file(tmp_path: Path):
    dsn_file = tmp_path / "database-dsn"
    dsn_file.write_text("postgresql://user:secret@localhost/db", encoding="utf-8")
    dsn_file.chmod(0o644)
    settings = Settings(
        database_dsn_file=dsn_file,
        db_path=tmp_path / "db.sqlite",
        token_file=tmp_path / "token",
    )

    posture = enforce_runtime_security(settings)

    assert posture["database_dsn_file_permissions"] == "owner_only"
    assert stat.S_IMODE(dsn_file.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name != "posix", reason="POSIX file mode test")
def test_existing_bearer_token_is_rehardened_before_use(tmp_path: Path):
    token_file = tmp_path / "token"
    token = "x" * 64
    token_file.write_text(token, encoding="utf-8")
    token_file.chmod(0o644)
    settings = Settings(db_path=tmp_path / "db.sqlite", token_file=token_file)

    assert ensure_token(settings) == token
    assert stat.S_IMODE(token_file.stat().st_mode) == 0o600
