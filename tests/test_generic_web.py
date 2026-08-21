from argus.sources.generic_web import GenericWebAdapter


def test_recursive_links_default_to_seed_host():
    allowed: set[str] = set()
    denied: set[str] = set()
    assert GenericWebAdapter._domain_allowed(
        "https://example.com/page", "example.com", allowed, denied
    )
    assert not GenericWebAdapter._domain_allowed(
        "https://external.example/page", "example.com", allowed, denied
    )


def test_explicit_allowed_domains_can_expand_scope():
    assert GenericWebAdapter._domain_allowed(
        "https://news.example.org/page",
        "example.com",
        {"example.org"},
        set(),
    )


def test_feed_autodiscovery_is_limited_to_rss_and_atom():
    html = """
    <html><head>
      <link rel="alternate" type="application/rss+xml" href="/feed.xml">
      <link rel="alternate" type="application/atom+xml" href="https://example.com/atom">
      <link rel="alternate" type="application/feed+json" href="/feed.json">
    </head></html>
    """
    feeds = GenericWebAdapter._feed_links(html, "https://example.com/news", "text/html")
    assert feeds == ["https://example.com/feed.xml", "https://example.com/atom"]
