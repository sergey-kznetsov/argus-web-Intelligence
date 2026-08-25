from argus.research.url_identity import canonicalize_discovery_url


def test_tracking_fragment_default_port_and_host_case_are_normalized():
    value = canonicalize_discovery_url(
        "HTTPS://Example.COM:443/path?a=1&utm_source=test&fbclid=x#section"
    )

    assert value == "https://example.com/path?a=1"


def test_empty_path_becomes_root_and_non_default_port_is_preserved():
    assert canonicalize_discovery_url("http://Example.com") == "http://example.com/"
    assert canonicalize_discovery_url("https://Example.com:8443") == "https://example.com:8443/"


def test_material_query_parameters_are_preserved_in_order():
    value = canonicalize_discovery_url("https://example.com/search?q=a&page=2")

    assert value == "https://example.com/search?q=a&page=2"


def test_unicode_host_is_canonicalized_with_idna():
    value = canonicalize_discovery_url("https://пример.рф/новости")

    assert value == "https://xn--e1afmkfd.xn--p1ai/новости"


def test_credentials_non_http_and_invalid_ports_are_rejected():
    assert canonicalize_discovery_url("https://user:pass@example.com/a") is None
    assert canonicalize_discovery_url("javascript:alert(1)") is None
    assert canonicalize_discovery_url("https://example.com:99999/a") is None
