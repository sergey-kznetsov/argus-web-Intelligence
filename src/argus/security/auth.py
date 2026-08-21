from __future__ import annotations

import hmac
import secrets
from pathlib import Path

from collections.abc import Awaitable, Callable

from fastapi import Header, HTTPException, status

from argus.config import Settings, get_settings


def ensure_token(settings: Settings) -> str:
    settings.ensure_dirs()
    if settings.token_file.exists():
        return settings.token_file.read_text(encoding="utf-8").strip()
    token = secrets.token_urlsafe(48)
    settings.token_file.write_text(token, encoding="utf-8")
    try:
        settings.token_file.chmod(0o600)
    except OSError:
        pass
    return token


def write_new_token(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(48)
    path.write_text(token, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return token


def bearer_dependency(settings: Settings) -> Callable[..., Awaitable[None]]:
    async def require(authorization: str | None = Header(default=None)) -> None:
        expected = ensure_token(settings)
        prefix = "Bearer "
        if not authorization or not authorization.startswith(prefix):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="authentication required")
        supplied = authorization[len(prefix):].strip()
        if not hmac.compare_digest(supplied, expected):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")
    return require


async def require_bearer(authorization: str | None = Header(default=None)) -> None:
    settings = get_settings()
    expected = ensure_token(settings)
    prefix = "Bearer "
    if not authorization or not authorization.startswith(prefix):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="authentication required")
    supplied = authorization[len(prefix):].strip()
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")
