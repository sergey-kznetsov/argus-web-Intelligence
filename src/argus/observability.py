from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from argus.security.redaction import redact_text

_EXTRA_FIELDS = (
    "event",
    "collection_id",
    "analysis_id",
    "consumer",
    "source_id",
    "stage",
    "status",
    "error_code",
)


class ArgusJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "severity": record.levelname,
            "logger": record.name,
            "message": redact_text(record.getMessage(), max_length=2000),
        }
        for field in _EXTRA_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = redact_text(value, max_length=500)
        if record.exc_info:
            payload["exception_type"] = record.exc_info[0].__name__
            payload["exception"] = redact_text(
                self.formatException(record.exc_info),
                max_length=4000,
            )
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(level: str = "INFO") -> None:
    logger = logging.getLogger("argus")
    logger.setLevel(level.upper())
    logger.propagate = False
    for handler in logger.handlers:
        if getattr(handler, "_argus_json_handler", False):
            handler.setLevel(level.upper())
            return
    handler = logging.StreamHandler()
    handler.setLevel(level.upper())
    handler.setFormatter(ArgusJsonFormatter())
    handler._argus_json_handler = True  # type: ignore[attr-defined]
    logger.addHandler(handler)
