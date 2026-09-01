from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator

from argus.consumer_registry import CONSUMER_PROFILE_REGISTRY
from argus.toolpacks import TOOL_PACK_REGISTRY

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
    max_duration_seconds: float = Field(default=240.0, ge=30.0, le=7_200.0)
    allowed_domains: list[str] = Field(default_factory=list)
    denied_domains: list[str] = Field(default_factory=list)
    seed_urls: list[HttpUrl] = Field(default_factory=list)
    source_pool_urls: list[HttpUrl] = Field(default_factory=list, max_length=100)
    language: str | None = None
    output_language: str = Field(default="ru", min_length=2, max_length=16)

    @field_validator("output_language")
    @classmethod
    def normalize_output_language(cls, value: str) -> str:
        normalized = value.strip().lower().replace("_", "-")
        if not normalized:
            raise ValueError("output_language must not be blank")
        return normalized


class CollectionRequest(BaseModel):
    protocol_version: Literal["1.0.0"] = PROTOCOL_VERSION
    consumer: str = Field(min_length=1, max_length=128)
    consumer_profile_version: int | None = Field(default=None, ge=1)
    capability: str | None = Field(default=None, min_length=1, max_length=128)
    requested_facts: list[str] = Field(default_factory=list, max_length=50)
    tool_pack_id: str | None = Field(default=None, min_length=1, max_length=128)
    tool_pack_version: int | None = Field(default=None, ge=1)
    analysis_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=256)
    territory: TerritoryContext
    intents: list[str] = Field(min_length=1, max_length=50)
    constraints: CollectionConstraints = Field(default_factory=CollectionConstraints)
    allow_partial: bool = True

    @field_validator("idempotency_key")
    @classmethod
    def normalize_idempotency_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("idempotency_key must not be blank")
        return normalized

    @model_validator(mode="after")
    def resolve_consumer_contract(self) -> "CollectionRequest":
        resolved = CONSUMER_PROFILE_REGISTRY.resolve(
            consumer=self.consumer,
            capability=self.capability,
            requested_facts=self.requested_facts,
            profile_version=self.consumer_profile_version,
        )
        self.consumer = resolved.consumer_id
        self.consumer_profile_version = resolved.profile_version
        self.capability = resolved.capability
        self.requested_facts = list(resolved.requested_facts)

        if resolved.legacy_unregistered:
            if self.tool_pack_id is not None or self.tool_pack_version is not None:
                raise ValueError(
                    "UNKNOWN_CONSUMER_TOOL_PACK: unregistered consumers cannot select tool packs"
                )
            self.tool_pack_id = None
            self.tool_pack_version = None
            return self

        if resolved.capability is None or resolved.tool_pack_id is None:
            raise ValueError("PROFILE_TOOL_PACK_MISSING: profiled consumer requires a tool pack")
        tool_pack = TOOL_PACK_REGISTRY.resolve(
            consumer_id=resolved.consumer_id,
            capability=resolved.capability,
            expected_tool_pack_id=resolved.tool_pack_id,
            requested_tool_pack_id=self.tool_pack_id,
            requested_version=self.tool_pack_version,
        )
        self.tool_pack_id = tool_pack.tool_pack_id
        self.tool_pack_version = tool_pack.version
        return self


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


class CollectionSummary(BaseModel):
    collection_id: str
    analysis_id: str
    consumer: str
    status: CollectionStatus
    created_at: datetime
    updated_at: datetime
    progress_percent: int = Field(ge=0, le=100)
    stage: str | None = None
    partial: bool
    error_count: int = Field(ge=0)
    observation_count: int = Field(ge=0)
    evidence_count: int = Field(ge=0)


class CollectionListPage(BaseModel):
    items: list[CollectionSummary]
    next_cursor: str | None = None


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


class ResultDeliveryLimits(BaseModel):
    full_result_max_items: int = Field(ge=1)
    full_result_max_bytes: int = Field(ge=1024)
    page_max_size: int = Field(ge=1)
    page_max_bytes: int = Field(ge=1024)


class CollectionResultSummary(BaseModel):
    protocol_version: Literal["1.0.0"] = PROTOCOL_VERSION
    collection_id: str
    analysis_id: str
    consumer: str
    status: CollectionStatus
    partial: bool
    observation_count: int = Field(ge=0)
    evidence_count: int = Field(ge=0)
    stored_bytes: int = Field(ge=0)
    full_result_available: bool
    delivery_limits: ResultDeliveryLimits
    coverage: list[SourceCoverage]
    errors: list[StructuredError]


class ObservationPage(BaseModel):
    protocol_version: Literal["1.0.0"] = PROTOCOL_VERSION
    collection_id: str
    status: CollectionStatus
    total_count: int = Field(ge=0)
    page_stored_bytes: int = Field(ge=0)
    items: list[Observation]
    next_cursor: str | None = None


class EvidencePage(BaseModel):
    protocol_version: Literal["1.0.0"] = PROTOCOL_VERSION
    collection_id: str
    status: CollectionStatus
    total_count: int = Field(ge=0)
    page_stored_bytes: int = Field(ge=0)
    items: list[Evidence]
    next_cursor: str | None = None
