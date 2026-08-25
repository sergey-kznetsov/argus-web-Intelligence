from __future__ import annotations

import os
import stat
from pathlib import Path

from argus.config import Settings

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def enforce_runtime_security(settings: Settings) -> dict[str, object]:
    """Validate application-level deployment invariants before services start.

    OS/container network namespaces, seccomp and cgroup policy remain deployment-level
    controls. ARGUS still refuses an accidental non-loopback application bind and
    hardens secret files it is able to manage on POSIX systems.
    """

    host = settings.host.strip().lower().strip("[]")
    if host not in _LOOPBACK_HOSTS:
        raise RuntimeError(
            "ARGUS must bind to a loopback host; expose it only through the internal "
            "Geo Analyzer integration/reverse-proxy boundary"
        )

    dsn_file_status = "not_configured"
    if settings.database_dsn_file is not None:
        dsn_file_status = harden_secret_file(settings.database_dsn_file, require_exists=True)

    token_file_status = "pending_creation"
    if settings.token_file.exists():
        token_file_status = harden_secret_file(settings.token_file, require_exists=True)

    return {
        "loopback_bind_enforced": True,
        "configured_host_is_loopback": True,
        "token_file_permissions": token_file_status,
        "database_dsn_file_permissions": dsn_file_status,
        "os_process_sandbox": "deployment_required",
        "network_egress_firewall": "deployment_required",
    }


def harden_secret_file(path: Path, *, require_exists: bool) -> str:
    """Best-effort ACL-independent hardening with strict POSIX verification."""

    try:
        path.stat()
    except FileNotFoundError:
        if require_exists:
            raise RuntimeError("required ARGUS secret file does not exist")
        return "missing"
    except OSError as exc:
        raise RuntimeError("ARGUS secret file cannot be inspected") from exc

    if os.name != "posix":
        # Windows permissions are ACL-based and cannot be represented by chmod mode
        # checks reliably. The module manager remains responsible for its ACL.
        return "platform_acl"

    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError as exc:
        raise RuntimeError("ARGUS secret file permissions cannot be hardened") from exc
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise RuntimeError("ARGUS secret file is accessible by group or other users")
    return "owner_only"
