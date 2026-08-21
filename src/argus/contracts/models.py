from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, HttpUrl, model_validator

PROTOCOL_VERSION = "1.0.0"


def utcnow() -> datetime:
    return datetime.now(UTC)


class CollectionStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Point(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class TerritoryContext(BaseModel):
    city: str | None = None
    address: str | None = None
    point: Point | None = None
    radius_meters: int | None = Field(default=None, gt=0, le=100_000)
    geometry: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_locator(self) -> "TerritoryContext":
        if not (self.city or self.address or self.point or self.geometry):
            raise ValueError("territory requires city, address, point or geometry")
        return self


class CollectionConstraints(BaseModel):
    max_pages: int = Field(default=30, ge=1, le=500)
    max_depth: int = Field(default=2, ge=0, le=5)
    allowed_domains: list[str] = Field(default_factory=list)
    denied_domains: list[str] = Field(default_factory=list)
    seed_urls: list[HttpUrl] = Field(default_factory=list)
    language: str | None = None


class CollectionRequest(BaseModel):
    protocol_version: Literal["1.0.0"] = PROTOCOL_VERSION
    consumer: str = Field(min_length=1, max_length=128)
    analysis_id: str = Field(min_length=1, max_length=128)
    territory: TerritoryContext
    intents: list[str] = Field(min_length=1, max_length=50)
    constraints: CollectionConstraints = Field(default_factory=CollectionConstraints)
    allow_partial: bool = True


class EvidenceSource(BaseModel):
    provider: str
    url: str
    collected_at: datetime
    source_id: str | None = None


class Evidence(BaseModel):
    evidence_id: str = Field(default_factory=lambda: str(uuid4()))
    observation_id: str | None = None
    type: str
    text: str
    source: EvidenceSource
    metadata: dict[str, Any] = Field(default_factory=dict)


class Observation(BaseModel):
    observation_id: str = Field(default_factory=lambda: str(uuid4()))
    collection_id: str
    analysis_id: str
    consumer: str
    source: str
    source_kind: str
    url: str
    entity_type: str
    entity_id: str | None = None
    title: str | None = None
    text: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    geo: Point | None = None
    published_at: datetime | None = None
    collected_at: datetime = Field(default_factory=utcnow)
    content_hash: str
    provenance: dict[str, Any] = Field(default_factory=dict)
    quality: dict[str, Any] = Field(default_factory=dict)


class Snapshot(BaseModel):
    snapshot_id: str = Field(default_factory=lambda: str(uuid4()))
    source_id: str
    source_url: str
    collected_at: datetime = Field(default_factory=utcnow)
    content_hash: str
    extractor_version: str
    content_type: str | None = None
    content: str
    previous_snapshot_id: str | None = None
    diff: str | None = None


class SourceCoverage(BaseModel):
    source_id: str
    status: str
    requested: bool = True
    observations: int = 0
    blocked: bool = False
    error_code: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class StructuredError(BaseModel):
    code: str
    message: str
    retryable: bool = False
    source_id: str | None = None


class CollectionRecord(BaseModel):
    collection_id: str
    request: CollectionRequest
    status: CollectionStatus
    created_at: datetime
    updated_at: datetime
    progress_percent: int = Field(default=0, ge=0, le=100)
    stage: str | None = None
    partial: bool = False
    coverage: list[SourceCoverage] = Field(default_factory=list)
    errors: list[StructuredError] = Field(default_factory=list)
    checkpoint: dict[str, Any] = Field(default_factory=dict)


class CollectionAccepted(BaseModel):
    collection_id: str
    status: CollectionStatus = CollectionStatus.QUEUED


class CollectionResult(BaseModel):
    protocol_version: Literal["1.0.0"] = PROTOCOL_VERSION
    collection_id: str
    analysis_id: str
    consumer: str
    status: CollectionStatus
    partial: bool
    observations: list[Observation]
    evidence: list[Evidence]
    coverage: list[SourceCoverage]
    errors: list[StructuredError]
