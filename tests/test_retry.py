from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

from argus.network.retry import retry_delay_seconds


def test_retry_delay_uses_exponential_fallback_and_cap():
    assert retry_delay_seconds(
        attempt=0,
        headers={},
        base_delay_seconds=2,
        max_delay_seconds=5,
    ) == 2
    assert retry_delay_seconds(
        attempt=1,
        headers={},
        base_delay_seconds=2,
        max_delay_seconds=5,
    ) == 4
    assert retry_delay_seconds(
        attempt=2,
        headers={},
        base_delay_seconds=2,
        max_delay_seconds=5,
    ) == 5


def test_retry_delay_prefers_numeric_retry_after_and_defers_when_too_long():
    assert retry_delay_seconds(
        attempt=0,
        headers={"Retry-After": "7"},
        base_delay_seconds=1,
        max_delay_seconds=10,
    ) == 7
    assert retry_delay_seconds(
        attempt=0,
        headers={"retry-after": "100"},
        base_delay_seconds=1,
        max_delay_seconds=10,
    ) is None


def test_retry_delay_accepts_http_date():
    now = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
    target = now + timedelta(seconds=9)
    assert retry_delay_seconds(
        attempt=0,
        headers={"Retry-After": format_datetime(target, usegmt=True)},
        base_delay_seconds=1,
        max_delay_seconds=30,
        now=now,
    ) == 9


def test_retry_delay_defers_http_date_beyond_local_maximum():
    now = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
    target = now + timedelta(minutes=10)
    assert retry_delay_seconds(
        attempt=0,
        headers={"Retry-After": format_datetime(target, usegmt=True)},
        base_delay_seconds=1,
        max_delay_seconds=30,
        now=now,
    ) is None
