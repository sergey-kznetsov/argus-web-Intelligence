from argus.config import Settings
from argus.contracts.models import CollectionRequest
from argus.crawler.models import FetchResult
from argus.sources.base import SourceTask
from argus.sources.sitemap import SitemapDiscoveryAdapter


def request(**constraint_values) -> CollectionRequest:
    constraints = {"max_depth": 2, "max_pages": 20}
    constraints.update(constraint_values)
    return CollectionRequest(
        consumer="test",
        analysis_id="1",
        territory={"city": "Ижевск"},
        intents=["local_news"],
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
        "https://example.com/sitemap.xml",
    ]
    assert all(task.source_id == "site_discovery" for task in result)


def test_missing_robots_still_tries_default_sitemap():
    result = adapter()._robots_tasks(robots_task(), None)
    assert [task.url for task in result] == ["https://example.com/sitemap.xml"]


def test_sitemap_index_is_same_host_bounded_and_one_level():
    source = adapter(sitemap_max_indexes=2)
    xml = """<?xml version="1.0"?>
    <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <sitemap><loc>https://example.com/a.xml</loc></sitemap>
      <sitemap><loc>https://evil.example/b.xml</loc></sitemap>
      <sitemap><loc>https://example.com/c.xml.gz</loc></sitemap>
      <sitemap><loc>https://example.com/d.xml</loc></sitemap>
      <sitemap><loc>https://example.com/e.xml</loc></sitemap>
    </sitemapindex>"""
    result = source._sitemap_tasks(
        sitemap_task(),
        fetched("https://example.com/sitemap.xml", xml),
        request(),
    )
    assert [task.url for task in result] == [
        "https://example.com/a.xml",
        "https://example.com/d.xml",
    ]

    nested = source._sitemap_tasks(
        sitemap_task("https://example.com/a.xml", index_depth=1),
        fetched("https://example.com/a.xml", xml),
        request(),
    )
    assert nested == []


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
