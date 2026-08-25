from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class WebSettings(BaseSettings):
    """Configuration for the optional operator-facing ARGUS web gateway."""

    model_config = SettingsConfigDict(
        env_prefix="ARGUS_WEB_",
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
    )

    host: str = "127.0.0.1"
    port: int = Field(default=8790, ge=1, le=65535)
    api_url: str = "http://127.0.0.1:8787"
    api_token_file: Path = Field(
        default=Path(".argus/token"),
        validation_alias=AliasChoices("ARGUS_WEB_API_TOKEN_FILE", "ARGUS_TOKEN_FILE"),
    )
    username: str = Field(default="argus", min_length=1, max_length=128)
    password_file: Path = Path(".argus/web-password")
    request_timeout_seconds: float = Field(default=45.0, gt=0, le=300)

    @field_validator("api_url", mode="before")
    @classmethod
    def validate_api_url(cls, value: object) -> str:
        url = str(value).strip().rstrip("/")
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("api_url must be an http(s) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("api_url must not contain credentials, query or fragment")
        if parsed.path not in {"", "/"}:
            raise ValueError("api_url must not contain an application path")
        host = parsed.hostname.casefold()
        if host not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("api_url must point to the local ARGUS API")
        return url

    def ensure_dirs(self) -> None:
        self.api_token_file.parent.mkdir(parents=True, exist_ok=True)
        self.password_file.parent.mkdir(parents=True, exist_ok=True)
