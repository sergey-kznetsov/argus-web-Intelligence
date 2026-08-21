from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ARGUS_", env_file=".env", extra="ignore")

    host: str = "127.0.0.1"
    port: int = Field(default=8787, ge=1, le=65535)
    token_file: Path = Path(".argus/token")
    db_path: Path = Path(".argus/argus.sqlite3")
    max_response_bytes: int = Field(default=5 * 1024 * 1024, ge=1024, le=100 * 1024 * 1024)
    http_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    http_max_redirects: int = Field(default=10, ge=0, le=30)
    browser_timeout_seconds: float = Field(default=45.0, gt=0, le=600)
    fetch_wait_timeout_seconds: float = Field(default=180.0, gt=0, le=3600)
    max_concurrency: int = Field(default=4, ge=1, le=64)
    browser_max_concurrency: int = Field(default=2, ge=1, le=16)
    fast_max_requests_per_minute: float = Field(default=120.0, gt=0, le=10_000)
    browser_max_requests_per_minute: float = Field(default=30.0, gt=0, le=2_000)
    per_domain_delay_seconds: float = Field(default=1.0, ge=0, le=300)
    throttled_domains: list[str] = Field(default_factory=list)
    throttle_base_delay_seconds: float = Field(default=2.0, gt=0, le=300)
    throttle_max_delay_seconds: float = Field(default=60.0, gt=0, le=3600)
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3:8b"
    agent_backend: str = "browser-use"
    agent_enabled: bool = False
    allow_internal_targets: list[str] = Field(default_factory=list)

    @field_validator("allow_internal_targets", "throttled_domains", mode="before")
    @classmethod
    def split_csv_lists(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip().lower() for item in value.split(",") if item.strip()]
        return value

    @field_validator("allow_internal_targets", "throttled_domains")
    @classmethod
    def normalize_hosts(cls, value: list[str]) -> list[str]:
        return sorted({item.lower().strip().strip(".") for item in value if item.strip()})

    @model_validator(mode="after")
    def validate_throttling(self) -> "Settings":
        if self.throttle_max_delay_seconds < self.throttle_base_delay_seconds:
            raise ValueError("throttle_max_delay_seconds must be >= throttle_base_delay_seconds")
        if self.browser_max_concurrency > self.max_concurrency:
            self.browser_max_concurrency = self.max_concurrency
        return self

    def ensure_dirs(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_file.parent.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings
