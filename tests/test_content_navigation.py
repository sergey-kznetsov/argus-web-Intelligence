from __future__ import annotations

from argus.contracts.models import CollectionRequest
from argus.crawler.models import FetchResult
from argus.research.content_navigation import ContentItemNavigationRanker
from argus.sources.atomic_content_web import AtomicContentWebAdapter
from argus.sources.base import SourceTask
from argus.sources.navigation_web import ContentNavigationOfficeAwareGenericWebAdapter


def _request(*, max_pages: int = 2) -> CollectionRequest:
    return CollectionRequest(
        consumer="content-navigation-test",
        analysis_id="content-navigation-test",
        territory={"city": "Ижевск", "address": "Пушкинская улица, 277"},
        intents=["local_news", "complaints"],
        constraints={"max_pages": max_pages, "max_depth": 1},
    )


def _fetched_listing() -> FetchResult:
    return FetchResult(
        url="https://example.org/news/",
        final_url="https://example.org/news/",
        status_code=200,
        content_type="text/html",
        text="<html><body>listing</body></html>",
        links=[
            "https://external.example/article/999",
            "https://example.org/login",
            "https://example.org/news/",
            "https://example.org/news/2026/09/09/water-main-break-pushkinskaya#comments",
            "https://example.org/topic/12345",
            "https://example.org/search?q=pushkinskaya",
        ],
    )


def _task(fetched: FetchResult) -> SourceTask:
    return SourceTask(
        source_id="generic_web",
        goal="local_news",
        url=fetched.final_url,
        depth=0,
        metadata={"research_goals": ["local_news", "complaints"]},
    )


def test_item_ranker_prefers_atomic_destinations_over_listing_and_service_shells():
    ranker = ContentItemNavigationRanker()
    urls = [
        "https://example.org/login",
        "https://example.org/news/",
        "https://example.org/archive/2026/",
        "https://example.org/search?q=pushkinskaya",
        "https://example.org/news/2026/09/09/water-main-break-pushkinskaya",
        "https://example.org/topic/12345",
    ]

    ranked = ranker.rank(urls)

    assert [item.url for item in ranked[:2]] == [
        "https://example.org/news/2026/09/09/water-main-break-pushkinskaya",
        "https://example.org/topic/12345",
    ]
    assert ranker.score("https://example.org/news/") < ranker.score(
        "https://example.org/topic/12345"
    )
    assert ranker.score("https://example.org/archive/2026/") < ranker.score(
        "https://example.org/news/2026/09/09/water-main-break-pushkinskaya"
    )


def test_navigation_shell_classifier_is_conservative_about_item_destinations():
    ranker = ContentItemNavigationRanker()

    assert ranker.is_navigation_shell("https://example.org/") is True
    assert ranker.is_navigation_shell("https://example.org/news/") is True
    assert ranker.is_navigation_shell("https://example.org/forum/") is True
    assert ranker.is_navigation_shell("https://example.org/category/city/") is True
    assert ranker.is_navigation_shell("https://example.org/search?q=pushkinskaya") is True
    assert ranker.is_navigation_shell("https://example.org/news?page=2") is True
    assert (
        ranker.is_navigation_shell(
            "https://example.org/news/2026/09/09/water-main-break-pushkinskaya"
        )
        is False
    )
    assert ranker.is_navigation_shell("https://example.org/topic/12345") is False
    assert ranker.is_navigation_shell("https://example.org/view?id=12345&page=2") is False


def test_generic_navigation_ranks_before_bounded_fanout_and_filters_external_links():
    adapter = object.__new__(ContentNavigationOfficeAwareGenericWebAdapter)
    adapter.sitemap_discovery_enabled = False
    fetched = _fetched_listing()

    discovered = adapter._discovered_tasks(
        _task(fetched),
        fetched,
        _request(max_pages=2),
        "collection-1",
    )
    generic = [item for item in discovered if item.source_id == "generic_web"]

    assert [item.url for item in generic] == [
        "https://example.org/news/2026/09/09/water-main-break-pushkinskaya",
        "https://example.org/topic/12345",
    ]
    assert all(item.metadata["content_navigation_is_evidence"] is False for item in generic)
    assert all(
        item.metadata["content_navigation_ranking_version"]
        == ContentItemNavigationRanker.version
        for item in generic
    )
    assert generic[0].metadata["content_navigation_score"] >= generic[1].metadata[
        "content_navigation_score"
    ]


def test_production_atomic_adapter_uses_same_item_first_navigation_policy():
    adapter = object.__new__(AtomicContentWebAdapter)
    adapter.sitemap_discovery_enabled = False
    fetched = _fetched_listing()

    discovered = adapter._discovered_tasks(
        _task(fetched),
        fetched,
        _request(max_pages=2),
        "collection-1",
    )
    generic = [item for item in discovered if item.source_id == "generic_web"]

    assert [item.url for item in generic] == [
        "https://example.org/news/2026/09/09/water-main-break-pushkinskaya",
        "https://example.org/topic/12345",
    ]
    assert all(item.metadata["content_navigation_is_evidence"] is False for item in generic)
