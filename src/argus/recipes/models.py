from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from argus.contracts.models import utcnow


class RecipeStep(BaseModel):
    action: Literal["goto", "click", "fill", "press", "wait", "scroll", "extract"]
    selector: str | None = None
    value: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class SiteRecipe(BaseModel):
    recipe_id: str = Field(default_factory=lambda: str(uuid4()))
    domain: str
    goal: str
    version: int = Field(default=1, ge=1)
    steps: list[RecipeStep]
    created_at: datetime = Field(default_factory=utcnow)
    last_success_at: datetime | None = None
    failures: int = 0
