from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from argus.contracts.models import utcnow


class RecipeStep(BaseModel):
    action: Literal["goto", "click", "fill", "press", "wait", "scroll", "extract"]
    selector: str | None = Field(default=None, max_length=2_000)
    value: str | None = Field(default=None, max_length=20_000)
    data: dict[str, Any] = Field(default_factory=dict)


class SiteRecipe(BaseModel):
    recipe_id: str = Field(default_factory=lambda: str(uuid4()))
    domain: str = Field(min_length=1, max_length=253)
    goal: str = Field(min_length=1, max_length=128)
    version: int = Field(default=1, ge=1)
    steps: list[RecipeStep] = Field(min_length=1, max_length=100)
    created_at: datetime = Field(default_factory=utcnow)

    # `active` is the backward-compatible default for recipes persisted before the
    # lifecycle model existed. New agent-generated recipes start as `candidate` and
    # are promoted only after a successful deterministic browser replay.
    status: Literal["candidate", "active", "invalidated"] = "active"
    verified_at: datetime | None = None
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    invalidated_at: datetime | None = None
    invalidation_reason: str | None = Field(default=None, max_length=256)

    # `failures` remains the legacy consecutive-failure counter so old stored JSON is
    # still valid. Totals were added separately for operational statistics.
    failures: int = Field(default=0, ge=0)
    successes: int = Field(default=0, ge=0)
    total_failures: int = Field(default=0, ge=0)
