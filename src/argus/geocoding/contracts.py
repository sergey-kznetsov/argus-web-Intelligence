from __future__ import annotations

from typing import Any, Protocol
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

from argus.contracts.models import Point, StructuredError


class GeocodeCandidate(BaseModel):
    provider: str = Field(min_length=1, max_length=64)
    provider_place_id: str | None = Field(default=None, max_length=512)
    display_name: str = Field(min_length=1, max_length=4_096)
    point: Point
    source_url: str = Field(min_length=1, max_length=8_192)
    importance: float | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)

    @field_validator("source_url")
    @classmethod
    def require_public_source_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("geocode source_url must be an http(s) URL")
        if parsed.username or parsed.password:
            raise ValueError("geocode source_url must not contain credentials")
        return value


class GeocodeResult(BaseModel):
    provider: str = Field(min_length=1, max_length=64)
    candidates: list[GeocodeCandidate] = Field(default_factory=list)
    blocked: bool = False
    errors: list[StructuredError] = Field(default_factory=list)


class GeocodeProvider(Protocol):
    provider_id: str

    async def search(
        self,
        query: str,
        *,
        limit: int = 3,
        language: str | None = None,
    ) -> GeocodeResult: ...

    async def health(self) -> dict[str, object]: ...
