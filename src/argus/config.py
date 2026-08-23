from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ARGUS_",
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
    )

    host: str = "127.0.0.1"
    port: int = Field(default=8787, ge=1, le=65535)
    token_file: Path = Path(".argus/token")
    execution_role: Literal["embedded", "api", "worker"] = "embedded"

    storage_backend: Literal["sqlite", "postgresql"] = "sqlite"
    db_path: Path = Path(".argus/argus.sqlite3")
    database_dsn: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("ARGUS_DATABASE_DSN", "GEOANALYZER_DATABASE_DSN"),
    )
    database_dsn_file: Path | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "ARGUS_DATABASE_DSN_FILE",
            "GEOANALYZER_DATABASE_DSN_FILE",
        ),
    )
    postgres_pool_min_size: int = Field(default=1, ge=0, le=32)
    postgres_pool_max_size: int = Field(default=8, ge=1, le=128)
    postgres_pool_timeout_seconds: float = Field(default=30.0, gt=0, le=300)

    worker_concurrency: int = Field(default=2, ge=1, le=32)
    worker_poll_interval_seconds: float = Field(default=1.0, gt=0, le=60)
    worker_lease_seconds: float = Field(default=90.0, ge=15, le=3600)
    worker_heartbeat_seconds: float = Field(default=20.0, ge=1, le=300)
    worker_health_max_age_seconds: float = Field(default=60.0, ge=5, le=600)

    queue_max_active_collections: int = Field(default=500, ge=1, le=100_000)
    queue_max_active_per_consumer: int = Field(default=100, ge=1, le=10_000)
    queue_retry_after_seconds: int = Field(default=15, ge=1, le=3600)

    idempotency_window_seconds: int = Field(default=86_400, ge=60, le=604_800)

    retention_maintenance_interval_seconds: int = Field(default=3600, ge=60, le=86_400)
    retention_collection_days: int = Field(default=180, ge=1, le=3650)
    retention_snapshot_days: int = Field(default=365, ge=1, le=3650)
    retention_worker_registration_days: int = Field(default=7, ge=1, le=3650)
    retention_batch_size: int = Field(default=500, ge=1, le=10_000)

    api_max_request_bytes: int = Field(default=1024 * 1024, ge=4096, le=16 * 1024 * 1024)
    api_full_result_max_items: int = Field(default=100, ge=1, le=5000)
    api_full_result_max_bytes: int = Field(
        default=4 * 1024 * 1024,
        ge=64 * 1024,
        le=100 * 1024 * 1024,
    )
    api_result_page_default_size: int = Field(default=50, ge=1, le=500)
    api_result_page_max_size: int = Field(default=100, ge=1, le=500)
    api_result_page_max_bytes: int = Field(
        default=2 * 1024 * 1024,
        ge=64 * 1024,
        le=32 * 1024 * 1024,
    )

    log_level: str = "INFO"
    max_response_bytes: int = Field(default=5 * 1024 * 1024, ge=1024, le=100 * 1024 * 1024)
    pdf_max_bytes: int = Field(default=5 * 1024 * 1024, ge=1024, le=50 * 1024 * 1024)
    pdf_max_pages: int = Field(default=60, ge=1, le=500)
    pdf_max_text_chars: int = Field(default=250_000, ge=1_000, le=2_000_000)
    pdf_extract_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    pdf_extract_memory_mb: int = Field(default=512, ge=128, le=4096)
    structured_data_max_bytes: int = Field(
        default=5 * 1024 * 1024,
        ge=1024,
        le=50 * 1024 * 1024,
    )
    structured_data_max_records: int = Field(default=1000, ge=1, le=100_000)
    structured_data_max_columns: int = Field(default=100, ge=1, le=10_000)
    structured_data_max_cell_chars: int = Field(default=10_000, ge=1, le=1_000_000)
    structured_data_max_json_depth: int = Field(default=32, ge=1, le=256)
    structured_data_max_json_nodes: int = Field(default=20_000, ge=1, le=1_000_000)
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
    sitemap_discovery_enabled: bool = True
    sitemap_max_urls: int = Field(default=20, ge=1, le=100)
    sitemap_max_indexes: int = Field(default=5, ge=1, le=20)

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
    def validate_limits(self) -> "Settings":
        if self.throttle_max_delay_seconds < self.throttle_base_delay_seconds:
            raise ValueError("throttle_max_delay_seconds must be >= throttle_base_delay_seconds")
        if self.direct_provider_retry_max_seconds < self.direct_provider_retry_base_seconds:
            raise ValueError(
                "direct_provider_retry_max_seconds must be >= direct_provider_retry_base_seconds"
            )
        if self.browser_max_concurrency > self.max_concurrency:
            self.browser_max_concurrency = self.max_concurrency
        if self.postgres_pool_min_size > self.postgres_pool_max_size:
            raise ValueError("postgres_pool_min_size must be <= postgres_pool_max_size")
        if self.worker_heartbeat_seconds >= self.worker_lease_seconds:
            raise ValueError("worker_heartbeat_seconds must be shorter than worker_lease_seconds")
        if self.queue_max_active_per_consumer > self.queue_max_active_collections:
            raise ValueError(
                "queue_max_active_per_consumer must be <= queue_max_active_collections"
            )
        if self.retention_snapshot_days < self.retention_collection_days:
            raise ValueError(
                "retention_snapshot_days must be >= retention_collection_days"
            )
        if self.api_result_page_default_size > self.api_result_page_max_size:
            raise ValueError(
                "api_result_page_default_size must be <= api_result_page_max_size"
            )
        if self.execution_role in {"api", "worker"} and self.storage_backend != "postgresql":
            raise ValueError("server api/worker roles require PostgreSQL storage")
        return self

    def database_dsn_value(self) -> str | None:
        if self.database_dsn_file is not None:
            try:
                value = self.database_dsn_file.read_text(encoding="utf-8").strip()
            except OSError:
                value = ""
            if value:
                return value
        if self.database_dsn is not None:
            value = self.database_dsn.get_secret_value().strip()
            if value:
                return value
        return None

    def ensure_dirs(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_file.parent.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings
