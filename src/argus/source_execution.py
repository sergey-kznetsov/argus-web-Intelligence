from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator


@dataclass(frozen=True, slots=True)
class SourceExecutionContext:
    """Minimal non-secret context that may be exposed with human interactions."""

    collection_id: str | None
    source_id: str | None


_CURRENT_SOURCE_EXECUTION: ContextVar[SourceExecutionContext | None] = ContextVar(
    "argus_source_execution",
    default=None,
)


def _normalize(value: object | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


@contextmanager
def source_execution_context(
    *,
    collection_id: object | None,
    source_id: object | None,
) -> Iterator[SourceExecutionContext]:
    """Bind one source fetch to its collection for nested runtimes.

    ContextVars keep concurrent source fetches isolated. Browser runtimes copy the current
    value into request-keyed state before handing work to Crawlee, because crawler handlers
    may execute in a different asyncio task.
    """

    context = SourceExecutionContext(
        collection_id=_normalize(collection_id),
        source_id=_normalize(source_id),
    )
    token = _CURRENT_SOURCE_EXECUTION.set(context)
    try:
        yield context
    finally:
        _CURRENT_SOURCE_EXECUTION.reset(token)


def current_source_execution_context() -> SourceExecutionContext | None:
    return _CURRENT_SOURCE_EXECUTION.get()
