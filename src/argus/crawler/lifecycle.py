from __future__ import annotations

import asyncio
from collections.abc import Callable
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from argus.crawler.errors import CrawlerRequestSkippedError
from argus.crawler.models import FetchResult


class FetchBroker:
    """Correlate Crawlee request outcomes with callers awaiting fetch results.

    Crawlee's normal handlers expose the request unique key, while
    ``on_skipped_request`` exposes only the URL and skip reason. The broker therefore
    keeps a bounded reverse URL index for currently pending calls so an intentional
    robots.txt/collision skip can complete the caller immediately instead of leaking
    into the outer fetch timeout.
    """

    def __init__(self) -> None:
        self._futures: dict[str, asyncio.Future[FetchResult]] = {}
        self._url_by_key: dict[str, str] = {}
        self._keys_by_url: dict[str, set[str]] = {}

    def create(self, url: str | None = None) -> tuple[str, asyncio.Future[FetchResult]]:
        key = str(uuid4())
        future = asyncio.get_running_loop().create_future()
        self._futures[key] = future
        if url:
            normalized = self._normalize_url(url)
            self._url_by_key[key] = normalized
            self._keys_by_url.setdefault(normalized, set()).add(key)
        return key, future

    def resolve(self, key: str, result: FetchResult) -> None:
        future = self._futures.get(key)
        if future is not None and not future.done():
            future.set_result(result)

    def reject(self, key: str, error: BaseException) -> None:
        future = self._futures.get(key)
        if future is not None and not future.done():
            future.set_exception(error)

    def reject_skipped(self, url: str, reason: object) -> int:
        """Reject all pending calls for a URL skipped by Crawlee.

        Multiple callers may legitimately await the same URL concurrently. A robots
        policy decision applies to every such request, so each waiter receives its own
        exception instance and can finish independently.
        """

        normalized = self._normalize_url(url)
        keys = tuple(self._keys_by_url.get(normalized, ()))
        for key in keys:
            self.reject(key, CrawlerRequestSkippedError(url, reason))
        return len(keys)

    def discard(self, key: str) -> None:
        self._futures.pop(key, None)
        normalized = self._url_by_key.pop(key, None)
        if normalized is None:
            return
        keys = self._keys_by_url.get(normalized)
        if keys is None:
            return
        keys.discard(key)
        if not keys:
            self._keys_by_url.pop(normalized, None)

    def reject_all(self, error: BaseException | Callable[[], BaseException]) -> None:
        for future in self._futures.values():
            if not future.done():
                value = error() if callable(error) else error
                future.set_exception(value)
        self._futures.clear()
        self._url_by_key.clear()
        self._keys_by_url.clear()

    @staticmethod
    def _normalize_url(url: str) -> str:
        parsed = urlsplit(str(url))
        scheme = parsed.scheme.casefold()
        host = (parsed.hostname or "").casefold().rstrip(".")
        port = parsed.port
        default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
        authority = host if port is None or default_port else f"{host}:{port}"
        path = parsed.path or "/"
        return urlunsplit((scheme, authority, path, parsed.query, ""))
