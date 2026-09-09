from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, unquote, urlsplit


@dataclass(frozen=True, slots=True)
class ContentNavigationCandidate:
    url: str
    score: int
    original_index: int


class ContentItemNavigationRanker:
    """Order crawl links toward likely item pages without making factual claims.

    This is navigation policy only. URL shape may decide which destination ARGUS fetches
    first, but it never changes an Observation type and never counts as Evidence.
    """

    version = "content-item-navigation/2"
    max_links_considered = 5_000

    _CONTENT_ROUTES = frozenset(
        {
            "appeal",
            "appeals",
            "article",
            "articles",
            "blog",
            "comment",
            "comments",
            "complaint",
            "complaints",
            "discussion",
            "discussions",
            "forum",
            "forums",
            "incident",
            "incidents",
            "message",
            "messages",
            "news",
            "novosti",
            "obrashchenie",
            "obrashcheniya",
            "post",
            "posts",
            "proisshestvie",
            "proisshestviya",
            "review",
            "reviews",
            "statya",
            "stati",
            "story",
            "stories",
            "thread",
            "threads",
            "topic",
            "topics",
            "zhaloba",
            "zhaloby",
        }
    )
    _NAVIGATION_ROUTES = frozenset(
        {
            "about",
            "account",
            "archive",
            "archives",
            "author",
            "authors",
            "category",
            "categories",
            "contact",
            "contacts",
            "feed",
            "help",
            "index",
            "login",
            "menu",
            "page",
            "pages",
            "register",
            "registration",
            "rss",
            "search",
            "service",
            "services",
            "signin",
            "signup",
            "sitemap",
            "tag",
            "tags",
        }
    )
    _POSITIVE_QUERY_KEYS = frozenset(
        {
            "article",
            "article_id",
            "id",
            "message",
            "news",
            "post",
            "post_id",
            "thread",
            "topic",
        }
    )
    _NEGATIVE_QUERY_KEYS = frozenset(
        {"category", "page", "paged", "search", "tag"}
    )
    _DATE_SEGMENT = re.compile(r"^(?:19|20)\d{2}$")
    _DAY_OR_MONTH = re.compile(r"^(?:0?[1-9]|[12]\d|3[01])$")
    _NUMERIC_ID = re.compile(r"^\d{3,}$")
    _UUID = re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
        flags=re.IGNORECASE,
    )

    def rank(self, urls: list[str]) -> list[ContentNavigationCandidate]:
        candidates = [
            ContentNavigationCandidate(
                url=url,
                score=self.score(url),
                original_index=index,
            )
            for index, url in enumerate(urls[: self.max_links_considered])
        ]
        return sorted(candidates, key=lambda item: (-item.score, item.original_index))

    def is_navigation_shell(self, url: str) -> bool:
        """Return True only for URL shapes that are clearly navigation/listing surfaces.

        This classification is deliberately conservative. It is used to suppress atomic
        fallback claims on obvious home/search/category/listing shells, never to decide the
        factual meaning of a destination page.
        """

        parsed = urlsplit(str(url))
        path = unquote(parsed.path).casefold()
        segments = [self._route_token(segment) for segment in path.split("/") if segment]
        segments = [segment for segment in segments if segment]
        query = {
            key.casefold(): value
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        }

        if not segments:
            return True
        if any(segment in self._NAVIGATION_ROUTES for segment in segments):
            return True
        if len(segments) == 1 and segments[0] in self._CONTENT_ROUTES:
            return True
        negative_query = bool(self._NEGATIVE_QUERY_KEYS.intersection(query))
        positive_query = bool(
            self._POSITIVE_QUERY_KEYS.intersection(query)
            and any(query.get(key) for key in self._POSITIVE_QUERY_KEYS)
        )
        return negative_query and not positive_query

    def score(self, url: str) -> int:
        parsed = urlsplit(str(url))
        path = unquote(parsed.path).casefold()
        segments = [segment for segment in path.split("/") if segment]
        if not segments:
            return -120

        normalized_segments = [self._route_token(segment) for segment in segments]
        normalized_segments = [segment for segment in normalized_segments if segment]
        score = 0

        content_positions = [
            index
            for index, segment in enumerate(normalized_segments)
            if segment in self._CONTENT_ROUTES
        ]
        navigation_positions = [
            index
            for index, segment in enumerate(normalized_segments)
            if segment in self._NAVIGATION_ROUTES
        ]

        # A bare section such as /news/ or /incidents/ is a useful listing, not an item.
        if len(normalized_segments) == 1 and content_positions:
            score -= 90
        elif content_positions:
            score += 45
            if any(index < len(normalized_segments) - 1 for index in content_positions):
                score += 35

        if navigation_positions:
            score -= 80
            if navigation_positions[-1] == len(normalized_segments) - 1:
                score -= 20

        if any(self._is_numeric_item_id(segment) for segment in normalized_segments):
            score += 35
        if any(self._UUID.fullmatch(segment) for segment in normalized_segments):
            score += 45
        if self._has_date_path(normalized_segments):
            score += 30

        last = normalized_segments[-1]
        if len(last) >= 12 and ("-" in segments[-1] or "_" in segments[-1]):
            score += 20
        if path.endswith((".html", ".htm", ".shtml")):
            score += 15

        query = {
            key.casefold(): value
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        }
        if self._POSITIVE_QUERY_KEYS.intersection(query) and any(query.values()):
            score += 35
        if self._NEGATIVE_QUERY_KEYS.intersection(query):
            score -= 55

        # Deeper paths are a weak item signal only after stronger navigation-shell penalties.
        score += min(15, max(0, len(normalized_segments) - 1) * 5)
        return score

    @classmethod
    def _is_numeric_item_id(cls, segment: str) -> bool:
        if not cls._NUMERIC_ID.fullmatch(segment):
            return False
        return cls._DATE_SEGMENT.fullmatch(segment) is None

    @classmethod
    def _has_date_path(cls, segments: list[str]) -> bool:
        for index, segment in enumerate(segments):
            if not cls._DATE_SEGMENT.fullmatch(segment):
                continue
            tail = segments[index + 1 : index + 2]
            if tail and cls._DAY_OR_MONTH.fullmatch(tail[0]):
                return True
        return False

    @staticmethod
    def _route_token(segment: str) -> str:
        value = segment.casefold().strip()
        if not value:
            return ""
        if "." in value:
            value = value.rsplit(".", 1)[0]
        return value
