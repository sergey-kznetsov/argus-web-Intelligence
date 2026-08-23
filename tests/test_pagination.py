from datetime import UTC, datetime

import pytest

from argus.pagination import (
    InvalidCursorError,
    decode_collection_cursor,
    encode_collection_cursor,
)


def test_collection_cursor_round_trip_normalizes_utc():
    created_at = datetime(2026, 8, 23, 12, 30, tzinfo=UTC)
    encoded = encode_collection_cursor(created_at, "collection-123")
    decoded = decode_collection_cursor(encoded)
    assert decoded.created_at == created_at
    assert decoded.collection_id == "collection-123"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "not-base64%%%",
        "e30",
        "A" * 2049,
    ],
)
def test_invalid_collection_cursor_is_rejected(value: str):
    with pytest.raises(InvalidCursorError):
        decode_collection_cursor(value)
