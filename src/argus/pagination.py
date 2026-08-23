from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import UTC, datetime

_CURSOR_VERSION = 1
_MAX_CURSOR_LENGTH = 2048


class InvalidCursorError(ValueError):
    """The supplied operations cursor is malformed or unsupported."""


@dataclass(frozen=True, slots=True)
class CollectionCursor:
    created_at: datetime
    collection_id: str


def encode_collection_cursor(created_at: datetime, collection_id: str) -> str:
    timestamp = created_at
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    else:
        timestamp = timestamp.astimezone(UTC)
    payload = json.dumps(
        {
            "v": _CURSOR_VERSION,
            "created_at": timestamp.isoformat(),
            "collection_id": collection_id,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_collection_cursor(value: str) -> CollectionCursor:
    cursor = value.strip()
    if not cursor or len(cursor) > _MAX_CURSOR_LENGTH:
        raise InvalidCursorError("invalid collection cursor")
    try:
        padding = "=" * (-len(cursor) % 4)
        decoded = base64.b64decode(
            cursor + padding,
            altchars=b"-_",
            validate=True,
        )
        payload = json.loads(decoded.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidCursorError("invalid collection cursor") from exc

    if not isinstance(payload, dict) or payload.get("v") != _CURSOR_VERSION:
        raise InvalidCursorError("unsupported collection cursor")
    raw_created_at = payload.get("created_at")
    raw_collection_id = payload.get("collection_id")
    if not isinstance(raw_created_at, str) or not isinstance(raw_collection_id, str):
        raise InvalidCursorError("invalid collection cursor")
    collection_id = raw_collection_id.strip()
    if not collection_id or len(collection_id) > 256:
        raise InvalidCursorError("invalid collection cursor")
    try:
        created_at = datetime.fromisoformat(raw_created_at)
    except ValueError as exc:
        raise InvalidCursorError("invalid collection cursor") from exc
    if created_at.tzinfo is None:
        raise InvalidCursorError("collection cursor timestamp must be timezone-aware")
    return CollectionCursor(
        created_at=created_at.astimezone(UTC),
        collection_id=collection_id,
    )
