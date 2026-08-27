from __future__ import annotations

from argus.security.urls import UnsafeUrlError


class CrawlerRequestSkippedError(UnsafeUrlError):
    """Raised when Crawlee intentionally skips a queued request before navigation.

    This is a transport/runtime access-policy outcome, not a fabricated HTTP response.
    It derives from ``UnsafeUrlError`` so existing FAST -> BROWSER -> AGENT escalation
    boundaries do not attempt to route around an explicit crawler access decision. In
    particular, ``robots_txt`` means ARGUS respected the remote site's declared crawl
    policy and did not fetch the target URL. Higher layers still inspect this concrete
    type to report the exact structured reason instead of a generic unsafe-URL failure.
    """

    def __init__(self, url: str, reason: object) -> None:
        self.url = str(url)
        raw_reason = getattr(reason, "value", reason)
        normalized = str(raw_reason or "unknown").strip().casefold().replace("-", "_")
        self.reason = normalized or "unknown"
        super().__init__(f"crawler skipped request ({self.reason})")

    @property
    def robots_txt(self) -> bool:
        return self.reason == "robots_txt"
