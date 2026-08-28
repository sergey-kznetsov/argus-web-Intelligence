from __future__ import annotations

from argus.config import Settings
from argus.contracts.models import CollectionRequest
from argus.research.territory_relevance import TerritoryRelevanceEvaluator
from argus.sources.sitemap import SitemapDiscoveryAdapter


def _request() -> CollectionRequest:
    return CollectionRequest(
        consumer="territory-navigation-test",
        analysis_id="territory-navigation-test",
        territory={"city": "Пермь", "address": "Комсомольский проспект, 27"},
        intents=["residential_population", "residential_premises_count"],
    )


def test_navigation_score_prefers_requested_city_over_homonyms_and_bare_house_number():
    evaluator = TerritoryRelevanceEvaluator()
    request = _request()

    perm_region = evaluator.navigation_url_score(
        "https://dom.mingkh.ru/permskiy-kray/",
        request,
    )
    perm_city = evaluator.navigation_url_score(
        "https://dom.mingkh.ru/permskiy-kray/perm/",
        request,
    )
    other_komsomolskiy = evaluator.navigation_url_score(
        "https://dom.mingkh.ru/altayskiy-kray/komsomolskiy/",
        request,
    )
    unrelated_27 = evaluator.navigation_url_score(
        "https://dom.mingkh.ru/murmanskaya-oblast/27-km-zheleznoy-dorogi/936689",
        request,
    )

    assert perm_region > other_komsomolskiy
    assert perm_city > other_komsomolskiy
    assert perm_city > unrelated_27


def test_sitemap_keeps_territory_score_for_later_queue_ordering():
    source = SitemapDiscoveryAdapter(
        Settings(sitemap_discovery_enabled=True, sitemap_max_urls=10),
        fast=object(),
    )
    request = _request()
    urls = [
        "https://dom.mingkh.ru/adygeya/",
        "https://dom.mingkh.ru/altayskiy-kray/komsomolskiy/",
        "https://dom.mingkh.ru/permskiy-kray/perm/123456",
        "https://dom.mingkh.ru/permskiy-kray/perm/",
        "https://dom.mingkh.ru/permskiy-kray/",
    ]

    ranked = source._rank_urls(urls, request)

    assert ranked[:3] == [
        "https://dom.mingkh.ru/permskiy-kray/",
        "https://dom.mingkh.ru/permskiy-kray/perm/",
        "https://dom.mingkh.ru/permskiy-kray/perm/123456",
    ]

    metadata = source._downstream_metadata(
        task=type(
            "Task",
            (),
            {"metadata": {"allowed_domains": ["dom.mingkh.ru"]}, "url": "sitemap.xml"},
        )(),
        request=request,
        url=ranked[1],
        collection_id="collection-1",
        rank=2,
    )
    assert metadata["discovery_navigation_score"] > 0
    assert (
        metadata["navigation_ranking_version"]
        == source.territory_relevance.navigation_ranking_version
    )
