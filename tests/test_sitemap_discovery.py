import gzip

from argus.config import Settings
from argus.contracts.models import CollectionRequest
from argus.crawler.models import FetchResult
from argus.sources.base import SourceTask
from argus.sources.sitemap import SitemapDiscoveryAdapter


def request(
    *,
    city: str = "Ижевск",
    address: str | None = None,
    intents: list[str] | None = None,
    **constraint_values,
) -> CollectionRequest:
    constraints = {"max_depth": 2, "max_pages": 20}
    constraints.update(constraint_values)
    return CollectionRequest(
        consumer="test",
        analysis_id="1",
        territory={"city": city, "address": address},
        intents=intents or ["local_news"],
        constraints=constraints,
    )


def adapter(**values) -> SitemapDiscoveryAdapter:
    payload = {
        "sitemap_discovery_enabled": True,
        "sitemap_max_urls": 2,
        "sitemap_max_indexes": 3,
    }
    payload.update(values)
    return SitemapDiscoveryAdapter(Settings(**payload), fast=object())


def robots_task() -> SourceTask:
    return SourceTask(
        source_id="site_discovery",
        goal="local_news",
        url="https://example.com/robots.txt",
        metadata={
            "collection_id": "collection-1",
            "site_discovery_kind": "robots",
            "root_host": "example.com",
            "root_origin": "https://example.com",
        },
    )


def sitemap_task(url="https://example.com/sitemap.xml", index_depth=0) -> SourceTask:
    return SourceTask(
        source_id="site_discovery",
        goal="local_news",
        url=url,
        metadata={
            "collection_id": "collection-1",
            "site_discovery_kind": "sitemap",
            "root_host": "example.com",
            "root_origin": "https://example.com",
            "sitemap_index_depth": index_depth,
        },
    )


def fetched(url: str, text: str, content_type="application/xml") -> FetchResult:
    return FetchResult(
        url=url,
        final_url=url,
        status_code=200,
        content_type=content_type,
        text=text,
        body=text.encode("utf-8"),
    )


def gzip_fetched(url: str, text: str) -> FetchResult:
    body = gzip.compress(text.encode("utf-8"))
    return FetchResult(
        url=url,
        final_url=url,
        status_code=200,
        content_type="application/gzip",
        text=body.decode("utf-8", errors="replace"),
        body=body,
    )


def test_robots_uses_declared_same_host_sitemaps_and_default():
    source = adapter()
    result = source._robots_tasks(
        robots_task(),
        fetched(
            "https://example.com/robots.txt",
            "\n".join(
                [
                    "User-agent: *",
                    "Sitemap: https://example.com/news.xml",
                    "Sitemap: https://other.example/sitemap.xml",
                    "Sitemap: https://example.com/archive.xml.gz",
                ]
            ),
            "text/plain",
        ),
    )

    assert [task.url for task in result] == [
        "https://example.com/news.xml",
        "https://example.com/archive.xml.gz",
        "https://example.com/sitemap.xml",
    ]
    assert all(task.source_id == "site_discovery" for task in result)


def test_missing_robots_still_tries_default_sitemap():
    result = adapter()._robots_tasks(robots_task(), None)
    assert [task.url for task in result] == ["https://example.com/sitemap.xml"]


def test_sitemap_index_is_same_host_bounded_and_recurses_within_request_depth():
    source = adapter(sitemap_max_indexes=2)
    xml = """<?xml version="1.0"?>
    <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <sitemap><loc>https://example.com/a.xml</loc></sitemap>
      <sitemap><loc>https://evil.example/b.xml</loc></sitemap>
      <sitemap><loc>https://example.com/c.xml.gz</loc></sitemap>
      <sitemap><loc>https://example.com/d.xml</loc></sitemap>
    </sitemapindex>"""
    req = request(max_depth=2)
    result = source._sitemap_tasks(
        sitemap_task(),
        fetched("https://example.com/sitemap.xml", xml),
        req,
    )
    assert [task.url for task in result] == [
        "https://example.com/a.xml",
        "https://example.com/c.xml.gz",
    ]
    assert all(task.metadata["sitemap_index_depth"] == 1 for task in result)

    nested = source._sitemap_tasks(
        sitemap_task("https://example.com/a.xml", index_depth=1),
        fetched("https://example.com/a.xml", xml),
        req,
    )
    assert [task.url for task in nested] == [
        "https://example.com/a.xml",
        "https://example.com/c.xml.gz",
    ]
    assert all(task.metadata["sitemap_index_depth"] == 2 for task in nested)

    exhausted = source._sitemap_tasks(
        sitemap_task("https://example.com/a.xml", index_depth=2),
        fetched("https://example.com/a.xml", xml),
        req,
    )
    assert exhausted == []


def test_urlset_emits_only_same_host_generic_web_tasks_with_limit():
    source = adapter(sitemap_max_urls=2)
    xml = """<?xml version="1.0"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://example.com/about</loc></url>
      <url><loc>https://other.example/escape</loc></url>
      <url><loc>https://example.com/local_news</loc></url>
      <url><loc>https://example.com/third</loc></url>
    </urlset>"""
    result = source._sitemap_tasks(
        sitemap_task(),
        fetched("https://example.com/sitemap.xml", xml),
        request(),
    )

    assert len(result) == 2
    assert result[0].url == "https://example.com/local_news"
    assert all(task.source_id == "generic_web" for task in result)
    assert all(task.metadata["discovery_provider"] == "sitemap" for task in result)
    assert all(task.metadata["disable_site_discovery"] is True for task in result)
    assert all(task.metadata["sitemap_navigation_only"] is True for task in result)


def test_urlset_can_route_relevant_pages_to_dedicated_source_without_becoming_evidence():
    source = adapter(sitemap_max_urls=2)
    task = SourceTask(
        source_id="site_discovery",
        goal="residential_premises_count",
        url="https://dom.mingkh.ru/sitemap.xml",
        metadata={
            "collection_id": "collection-1",
            "site_discovery_kind": "sitemap",
            "root_host": "dom.mingkh.ru",
            "root_origin": "https://dom.mingkh.ru",
            "sitemap_index_depth": 0,
            "site_discovery_target_source_id": "mingkh_residential",
            "allowed_domains": ["dom.mingkh.ru"],
            "research_goals": ["residential_population", "residential_premises_count"],
            "research_input_candidates": [
                "Пермь, Комсомольский проспект, 27",
                "Комсомольский проспект, 27",
            ],
            "research_input_candidates_navigation_only": True,
            "research_input_candidates_are_evidence": False,
            "research_input_scope": "territory_context",
            "dedicated_source_direct_entry": True,
            "dedicated_source_navigation": "robots_sitemap",
            "source_policy": "mandatory_single_factual_source",
        },
    )
    xml = """<?xml version="1.0"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://dom.mingkh.ru/moskva/cao/123456</loc></url>
      <url><loc>https://dom.mingkh.ru/permskiy-kray/perm/422906</loc></url>
      <url><loc>https://dom.mingkh.ru/permskiy-kray/perm/komsomolskiy-prospekt</loc></url>
    </urlset>"""
    req = request(
        city="Пермь",
        address="Комсомольский проспект, 27",
        intents=["residential_population", "residential_premises_count"],
    )

    result = source._sitemap_tasks(task, fetched(task.url, xml), req)

    assert len(result) == 2
    assert result[0].url == "https://dom.mingkh.ru/permskiy-kray/perm/komsomolskiy-prospekt"
    assert result[0].source_id == "mingkh_residential"
    assert result[0].metadata["research_goals"] == [
        "residential_population",
        "residential_premises_count",
    ]
    assert result[0].metadata["research_input_scope"] == "territory_context"
    assert result[0].metadata["research_input_candidates_are_evidence"] is False
    assert result[0].metadata["sitemap_navigation_only"] is True
    assert result[0].metadata["sitemap_source_url"] == task.url
    assert result[0].metadata["allowed_domains"] == ["dom.mingkh.ru"]


def test_invalid_or_recursive_target_source_id_falls_back_to_generic_web():
    source = adapter(sitemap_max_urls=1)
    xml = """<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://example.com/local_news</loc></url>
    </urlset>"""
    for target in ["site_discovery", "../unsafe", "Generic Web"]:
        task = sitemap_task()
        task.metadata["site_discovery_target_source_id"] = target
        result = source._sitemap_tasks(task, fetched(task.url, xml), request())
        assert result[0].source_id == "generic_web"


def test_gzip_urlset_is_decompressed_with_same_navigation_rules():
    xml = """<?xml version="1.0"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://example.com/local_news</loc></url>
      <url><loc>https://other.example/escape</loc></url>
    </urlset>"""

    result = adapter()._sitemap_tasks(
        sitemap_task("https://example.com/sitemap.xml.gz"),
        gzip_fetched("https://example.com/sitemap.xml.gz", xml),
        request(),
    )

    assert [task.url for task in result] == ["https://example.com/local_news"]
    assert result[0].source_id == "generic_web"
    assert result[0].metadata["discovery_provider"] == "sitemap"


def test_gzip_sitemap_expansion_over_response_limit_is_ignored():
    source = adapter(max_response_bytes=1024)
    xml = (
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + "<!--"
        + ("x" * 2000)
        + "-->"
        + "<url><loc>https://example.com/local_news</loc></url></urlset>"
    )
    compressed = gzip_fetched("https://example.com/sitemap.xml.gz", xml)
    assert len(compressed.body or b"") < source.settings.max_response_bytes

    result = source._sitemap_tasks(
        sitemap_task("https://example.com/sitemap.xml.gz"),
        compressed,
        request(),
    )

    assert result == []


def test_malicious_sitemap_xml_is_ignored_without_entity_expansion():
    xml = """<!DOCTYPE urlset [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>&xxe;</loc></url>
    </urlset>"""
    result = adapter()._sitemap_tasks(
        sitemap_task(),
        fetched("https://example.com/sitemap.xml", xml),
        request(),
    )
    assert result == []
