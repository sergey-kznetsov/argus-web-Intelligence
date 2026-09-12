from __future__ import annotations


_CAPTCHA_MARKERS = (
    "captcha",
    "verify you are human",
    "robot check",
    "i'm not a robot",
    "i am not a robot",
    "введите код с картинки",
    "введите символы с картинки",
    "подтвердите, что вы не робот",
    "я не робот",
)

_TRANSIENT_CHALLENGE_MARKERS = (
    "checking your browser",
    "checking if the site connection is secure",
    "checking if the connection is secure",
    "performing security verification",
    "security verification in progress",
    "please wait while we verify",
    "enable cookies to continue",
    "cf-chl-",
)

_STRONG_BLOCK_MARKERS = (
    *_CAPTCHA_MARKERS,
    *_TRANSIENT_CHALLENGE_MARKERS,
    "access denied",
)

_SHORT_INTERSTITIAL_MARKERS = (
    "just a moment",
    "checking...",
    "checking…",
    "please wait",
)


def _html_sample(text: str, content_type: str | None) -> str | None:
    if content_type and "html" not in content_type.casefold():
        return None
    return " ".join(text[:50_000].casefold().split())


def looks_like_captcha_page(text: str, content_type: str | None = None) -> bool:
    """Return True for explicit user-verification/CAPTCHA markers."""

    sample = _html_sample(text, content_type)
    return sample is not None and any(marker in sample for marker in _CAPTCHA_MARKERS)


def looks_like_transient_challenge_page(
    text: str,
    content_type: str | None = None,
) -> bool:
    """Return True for browser challenges that may clear through normal page execution.

    These are not CAPTCHA answers. ARGUS may keep the same browser session alive, allow
    site JavaScript/cookies to complete, and perform a bounded ordinary reload. If the
    challenge remains or turns into an explicit CAPTCHA, control is handed to the user.
    """

    sample = _html_sample(text, content_type)
    if sample is None:
        return False
    if any(marker in sample for marker in _TRANSIENT_CHALLENGE_MARKERS):
        return True
    return len(sample) <= 2_000 and any(
        marker in sample for marker in _SHORT_INTERSTITIAL_MARKERS
    )


def looks_like_blocked_page(text: str, content_type: str | None = None) -> bool:
    """Identify access/challenge shells that must never become factual Evidence."""

    sample = _html_sample(text, content_type)
    if sample is None:
        return False
    if any(marker in sample for marker in _STRONG_BLOCK_MARKERS):
        return True
    if len(sample) <= 2_000 and any(marker in sample for marker in _SHORT_INTERSTITIAL_MARKERS):
        return True
    return False


__all__ = [
    "looks_like_blocked_page",
    "looks_like_captcha_page",
    "looks_like_transient_challenge_page",
]
