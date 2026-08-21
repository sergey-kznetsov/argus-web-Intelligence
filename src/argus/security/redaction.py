from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(token|api[_-]?key|password|passwd|secret|authorization|access[_-]?token)"
    r"(\s*[:=]\s*)([^\s,;]+)"
)
_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)


def redact_url(value: str) -> str:
    """Remove userinfo, query and fragment from an HTTP(S) URL for safe diagnostics."""

    try:
        parsed = urlsplit(value)
    except ValueError:
        return "[REDACTED_URL]"
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return "[REDACTED_URL]"
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    try:
        port = parsed.port
    except ValueError:
        port = None
    netloc = f"{host}:{port}" if port is not None else host
    return urlunsplit((parsed.scheme.lower(), netloc, parsed.path, "", ""))


def redact_text(value: object, max_length: int = 1000) -> str:
    """Redact common secret forms and URL credentials/query parameters from diagnostic text."""

    text = str(value)
    text = _BEARER_RE.sub("Bearer [REDACTED]", text)
    text = _ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", text)
    text = _URL_RE.sub(lambda match: redact_url(match.group(0)), text)
    if len(text) > max_length:
        return text[:max_length] + "…"
    return text


def safe_error_message(error: BaseException, max_length: int = 500) -> str:
    message = redact_text(error, max_length=max_length)
    return message or type(error).__name__
