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
) -> float | None:
    """Return a safe retry delay or ``None`` when ARGUS must not retry yet.

    Invalid/missing ``Retry-After`` falls back to bounded exponential backoff. A valid
    explicit ``Retry-After`` is authoritative: if it exceeds ARGUS' configured maximum
    wait, the caller must stop retrying this operation rather than retry early.
    """

    retry_after = headers.get("retry-after") or headers.get("Retry-After")
    if retry_after:
        parsed = _retry_after_seconds(retry_after, now=now)
        if parsed is not None:
            delay = max(0.0, parsed)
            if delay > max_delay_seconds:
                return None
            return delay
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
