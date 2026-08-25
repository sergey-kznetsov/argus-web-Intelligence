from __future__ import annotations

import hmac
import os
import secrets
from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import Header, HTTPException, status

from argus.config import Settings, get_settings
from argus.security.runtime_posture import harden_secret_file

_MIN_TOKEN_LENGTH = 32


def _read_valid_token(path: Path) -> str | None:
    try:
        token = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    if len(token) < _MIN_TOKEN_LENGTH:
        return None
    return token


def _write_token_atomic(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(48)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        temporary.write_text(token, encoding="utf-8")
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        os.replace(temporary, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
        harden_secret_file(path, require_exists=True)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return token


def ensure_token(settings: Settings) -> str:
    settings.ensure_dirs()
    existing = _read_valid_token(settings.token_file)
    if existing is not None:
        harden_secret_file(settings.token_file, require_exists=True)
        return existing
    return _write_token_atomic(settings.token_file)


def write_new_token(path: Path) -> str:
    return _write_token_atomic(path)


def bearer_dependency(settings: Settings) -> Callable[..., Awaitable[None]]:
    async def require(authorization: str | None = Header(default=None)) -> None:
        expected = ensure_token(settings)
        scheme, separator, supplied = (authorization or "").partition(" ")
        if not separator or scheme.lower() != "bearer" or not supplied.strip():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="authentication required",
            )
        if not hmac.compare_digest(supplied.strip(), expected):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid credentials",
            )

    return require


async def require_bearer(authorization: str | None = Header(default=None)) -> None:
    settings = get_settings()
    expected = ensure_token(settings)
    scheme, separator, supplied = (authorization or "").partition(" ")
    if not separator or scheme.lower() != "bearer" or not supplied.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
        )
    if not hmac.compare_digest(supplied.strip(), expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid credentials",
        )
