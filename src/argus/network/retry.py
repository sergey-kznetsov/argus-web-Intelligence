from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Mapping

RETRYABLE_PROVIDER_STATUSES = frozenset({429, 503})


def retry_delay_seconds(
    *,
    attempt: int,
    headers: Mapping[str, str],
    base_delay_seconds: float,
    max_delay_seconds: float,
    now: datetime | None = None,
) -> float:
    """Return a bounded retry delay, preferring Retry-After when it is valid."""

    retry_after = headers.get("retry-after") or headers.get("Retry-After")
    if retry_after:
        parsed = _retry_after_seconds(retry_after, now=now)
        if parsed is not None:
            return min(max_delay_seconds, max(0.0, parsed))
    exponential = base_delay_seconds * (2 ** max(0, attempt))
    return min(max_delay_seconds, max(0.0, exponential))


def _retry_after_seconds(value: str, *, now: datetime | None = None) -> float | None:
    stripped = value.strip()
    try:
        return float(stripped)
    except ValueError:
        pass

    try:
        target = parsedate_to_datetime(stripped)
    except (TypeError, ValueError, OverflowError):
        return None
    if target.tzinfo is None:
        target = target.replace(tzinfo=UTC)
    current = now or datetime.now(UTC)
    return (target - current).total_seconds()
