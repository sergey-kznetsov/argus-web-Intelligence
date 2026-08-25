from argus.crawler.browser.runtime import BrowserCrawlerRuntime


def test_browser_security_options_preserve_sandbox_and_isolate_contexts():
    options = BrowserCrawlerRuntime.security_options()

    assert options["browser_type"] == "chromium"
    assert options["use_incognito_pages"] is True
    assert options["browser_launch_options"] == {"chromium_sandbox": True}
    assert options["browser_new_context_options"] == {
        "accept_downloads": False,
        "service_workers": "block",
        "ignore_https_errors": False,
    }

    serialized = repr(options).lower()
    assert "--no-sandbox" not in serialized
    assert "chromium_sandbox': false" not in serialized
