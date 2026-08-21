from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class FetchResult:
    url: str
    final_url: str
    status_code: int
    content_type: str | None
    text: str
    title: str | None = None
    links: list[str] = field(default_factory=list)
    blocked: bool = False
    runtime: str = "fast"
