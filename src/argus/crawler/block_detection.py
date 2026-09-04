from __future__ import annotations


_STRONG_BLOCK_MARKERS = (
    "captcha",
    "verify you are human",
    "access denied",
    "robot check",
    "checking your browser",
    "checking if the site connection is secure",
    "checking if the connection is secure",
    "performing security verification",
    "security verification in progress",
    "please wait while we verify",
    "enable cookies to continue",
    "cf-chl-",
)

_SHORT_INTERSTITIAL_MARKERS = (
    "just a moment",
    "checking...",
    "checking…",
    "please wait",
)


def looks_like_blocked_page(text: str, content_type: str | None = None) -> bool:
    """Identify access/challenge shells that must never become factual Evidence.

    The detector stays deliberately generic and conservative. Strong challenge markers are
    accepted on any HTML page; short ambiguous phrases only count when the visible payload
    itself is tiny, which avoids classifying normal articles containing "please wait" as
    blocked pages.
    """

    if content_type and "html" not in content_type.casefold():
        return False

    sample = " ".join(text[:50_000].casefold().split())
    if any(marker in sample for marker in _STRONG_BLOCK_MARKERS):
        return True

    if len(sample) <= 2_000 and any(marker in sample for marker in _SHORT_INTERSTITIAL_MARKERS):
        return True
    return False


__all__ = ["looks_like_blocked_page"]
