from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

_CURSOR_VERSION = 1
_MAX_CURSOR_LENGTH = 2048


class InvalidCursorError(ValueError):
    """The supplied pagination cursor is malformed or unsupported."""


@dataclass(frozen=True, slots=True)
class CollectionCursor:
    created_at: datetime
    collection_id: str


@dataclass(frozen=True, slots=True)
class ResultCursor:
    collection_id: str
    kind: Literal["observation", "evidence"]
    item_id: str


def _encode(payload: dict[str, object]) -> str:
    body = json.dumps(
        payload,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(body).decode("ascii").rstrip("=")


def _decode(value: str) -> dict[str, object]:
    cursor = value.strip()
    if not cursor or len(cursor) > _MAX_CURSOR_LENGTH:
        raise InvalidCursorError("invalid pagination cursor")
    try:
        padding = "=" * (-len(cursor) % 4)
        decoded = base64.b64decode(
            cursor + padding,
            altchars=b"-_",
            validate=True,
        )
        payload = json.loads(decoded.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidCursorError("invalid pagination cursor") from exc
    if not isinstance(payload, dict) or payload.get("v") != _CURSOR_VERSION:
        raise InvalidCursorError("unsupported pagination cursor")
    return payload


def encode_collection_cursor(created_at: datetime, collection_id: str) -> str:
    timestamp = created_at
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    else:
        timestamp = timestamp.astimezone(UTC)
    return _encode(
        {
            "v": _CURSOR_VERSION,
            "type": "collection",
            "created_at": timestamp.isoformat(),
            "collection_id": collection_id,
        }
    )


def decode_collection_cursor(value: str) -> CollectionCursor:
    payload = _decode(value)
    if payload.get("type") not in {None, "collection"}:
        raise InvalidCursorError("invalid collection cursor")
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


def encode_result_cursor(
    collection_id: str,
    kind: Literal["observation", "evidence"],
    item_id: str,
) -> str:
    return _encode(
        {
            "v": _CURSOR_VERSION,
            "type": "result",
            "collection_id": collection_id,
            "kind": kind,
            "item_id": item_id,
        }
    )


def decode_result_cursor(
    value: str,
    *,
    collection_id: str,
    kind: Literal["observation", "evidence"],
) -> ResultCursor:
    payload = _decode(value)
    raw_collection_id = payload.get("collection_id")
    raw_kind = payload.get("kind")
    raw_item_id = payload.get("item_id")
    if payload.get("type") != "result":
        raise InvalidCursorError("invalid result cursor")
    if raw_collection_id != collection_id or raw_kind != kind:
        raise InvalidCursorError("result cursor does not match requested collection/kind")
    if not isinstance(raw_item_id, str):
        raise InvalidCursorError("invalid result cursor")
    item_id = raw_item_id.strip()
    if not item_id or len(item_id) > 512:
        raise InvalidCursorError("invalid result cursor")
    return ResultCursor(
        collection_id=collection_id,
        kind=kind,
        item_id=item_id,
    )
