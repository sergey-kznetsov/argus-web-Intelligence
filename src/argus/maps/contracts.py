from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator, model_validator

from argus.contracts.models import Point, StructuredError, TerritoryContext, utcnow


class MapSearchRequest(BaseModel):
    """Provider-neutral public map search request.

    This contract intentionally describes collection mechanics only. Consumer-specific
    scoring, competition logic and market interpretation stay outside ARGUS.
    """

    territory: TerritoryContext
    query: str | None = Field(default=None, max_length=256)
    categories: list[str] = Field(default_factory=list, max_length=50)
    radius_meters: int | None = Field(default=None, gt=0, le=100_000)
    limit: int = Field(default=20, ge=1, le=100)
    cursor: str | None = Field(default=None, max_length=2_048)
    language: str | None = Field(default=None, max_length=32)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_search_dimension(self) -> "MapSearchRequest":
        if not (self.query and self.query.strip()) and not self.categories:
            raise ValueError("map search requires query or categories")
        return self


class MapProviderCapabilities(BaseModel):
    text_search: bool = True
    category_search: bool = True
    nearby: bool = True
    pagination: bool = False
    place_details: bool = False
    public_web_only: bool = True


class MapPlace(BaseModel):
    provider: str = Field(min_length=1, max_length=64)
    provider_place_id: str | None = Field(default=None, max_length=512)
    name: str = Field(min_length=1, max_length=1_024)
    address: str | None = Field(default=None, max_length=2_048)
    point: Point | None = None
    categories: list[str] = Field(default_factory=list, max_length=100)
    source_url: str = Field(min_length=1, max_length=8_192)
    collected_at: datetime = Field(default_factory=utcnow)
    attributes: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)

    @field_validator("source_url")
    @classmethod
    def require_public_source_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("map source_url must be an http(s) URL")
        if parsed.username or parsed.password:
            raise ValueError("map source_url must not contain credentials")
        return value


class MapSearchResult(BaseModel):
    provider: str = Field(min_length=1, max_length=64)
    places: list[MapPlace] = Field(default_factory=list)
    next_cursor: str | None = Field(default=None, max_length=2_048)
    blocked: bool = False
    partial: bool = False
    errors: list[StructuredError] = Field(default_factory=list)
