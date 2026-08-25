from __future__ import annotations

import os
import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from argus.web.config import WebSettings

_basic = HTTPBasic(auto_error=False)


def ensure_web_password(settings: WebSettings) -> str:
    settings.ensure_dirs()
    try:
        existing = settings.password_file.read_text(encoding="utf-8").strip()
    except OSError:
        existing = ""
    if existing:
        return existing

    password = secrets.token_urlsafe(32)
    settings.password_file.write_text(password + "\n", encoding="utf-8")
    try:
        os.chmod(settings.password_file, 0o600)
    except OSError:
        pass
    return password


def basic_auth_dependency(settings: WebSettings):
    expected_password = ensure_web_password(settings)
    expected_username = settings.username

    async def dependency(
        credentials: Annotated[HTTPBasicCredentials | None, Depends(_basic)],
    ) -> str:
        if credentials is None:
            raise _unauthorized()
        valid_user = secrets.compare_digest(credentials.username, expected_username)
        valid_password = secrets.compare_digest(credentials.password, expected_password)
        if not (valid_user and valid_password):
            raise _unauthorized()
        return credentials.username

    return dependency


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="authentication required",
        headers={"WWW-Authenticate": 'Basic realm="ARGUS Web UI"'},
    )
