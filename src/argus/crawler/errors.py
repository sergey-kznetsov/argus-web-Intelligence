from __future__ import annotations


class CrawlerRequestSkippedError(RuntimeError):
    """Raised when Crawlee intentionally skips a queued request before navigation.

    This is a transport/runtime outcome, not a fabricated HTTP response. In particular,
    ``robots_txt`` means ARGUS respected the remote site's declared crawl policy and did
    not fetch the target URL. Callers may map the reason to their own structured status.
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
