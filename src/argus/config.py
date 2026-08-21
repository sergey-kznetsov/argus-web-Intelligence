from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ARGUS_", env_file=".env", extra="ignore")

    host: str = "127.0.0.1"
    port: int = Field(default=8787, ge=1, le=65535)
    token_file: Path = Path(".argus/token")
    db_path: Path = Path(".argus/argus.sqlite3")
    log_level: str = "INFO"
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

    direct_provider_max_retries: int = Field(default=2, ge=0, le=5)
    direct_provider_retry_base_seconds: float = Field(default=1.0, ge=0, le=300)
    direct_provider_retry_max_seconds: float = Field(default=30.0, ge=0, le=3600)

    discovery_max_queries: int = Field(default=8, ge=1, le=50)
    searxng_url: str | None = None
    searxng_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    searxng_max_results_per_query: int = Field(default=10, ge=1, le=50)
    browser_serp_enabled: bool = True
    browser_serp_max_results_per_query: int = Field(default=5, ge=1, le=20)
    browser_serp_wait_ms: int = Field(default=750, ge=250, le=5_000)

    overpass_url: str | None = None
    overpass_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    overpass_min_interval_seconds: float = Field(default=1.0, ge=0, le=300)
    nominatim_url: str | None = None
    nominatim_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    nominatim_max_results: int = Field(default=3, ge=1, le=10)
    nominatim_min_interval_seconds: float = Field(default=1.0, ge=0, le=300)

    wayback_cdx_url: str | None = None
    wayback_capture_base_url: str = "https://web.archive.org/web"
    wayback_timeout_seconds: float = Field(default=20.0, gt=0, le=300)
    wayback_max_captures: int = Field(default=5, ge=1, le=20)
    wayback_min_interval_seconds: float = Field(default=2.0, ge=0, le=300)

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

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: object) -> str:
        level = str(value).upper().strip()
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if level not in allowed:
            raise ValueError(f"log_level must be one of {sorted(allowed)}")
        return level

    @field_validator(
        "searxng_url",
        "overpass_url",
        "nominatim_url",
        "wayback_cdx_url",
        "wayback_capture_base_url",
        mode="before",
    )
    @classmethod
    def normalize_service_url(cls, value: object) -> str | None:
        if value is None or not str(value).strip():
            return None
        url = str(value).strip().rstrip("/")
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("service URL must be an http(s) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("service URL must not contain credentials, query or fragment")
        return url

    @model_validator(mode="after")
    def validate_throttling(self) -> "Settings":
        if self.throttle_max_delay_seconds < self.throttle_base_delay_seconds:
            raise ValueError("throttle_max_delay_seconds must be >= throttle_base_delay_seconds")
        if self.direct_provider_retry_max_seconds < self.direct_provider_retry_base_seconds:
            raise ValueError(
                "direct_provider_retry_max_seconds must be >= direct_provider_retry_base_seconds"
            )
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
