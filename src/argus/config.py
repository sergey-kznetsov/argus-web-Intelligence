from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ARGUS_", env_file=".env", extra="ignore")

    host: str = "127.0.0.1"
    port: int = 8787
    token_file: Path = Path(".argus/token")
    db_path: Path = Path(".argus/argus.sqlite3")
    max_response_bytes: int = 5 * 1024 * 1024
    http_timeout_seconds: float = 30.0
    browser_timeout_seconds: float = 45.0
    max_concurrency: int = 4
    per_domain_delay_seconds: float = 1.0
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3:8b"
    agent_backend: str = "browser-use"
    agent_enabled: bool = False
    allow_internal_targets: list[str] = []

    @field_validator("allow_internal_targets", mode="before")
    @classmethod
    def split_internal_targets(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip().lower() for item in value.split(",") if item.strip()]
        return value

    def ensure_dirs(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_file.parent.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings
