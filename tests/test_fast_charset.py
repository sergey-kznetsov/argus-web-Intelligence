from argus.crawler.fast.runtime import FastCrawlerRuntime


def test_fast_runtime_falls_back_to_utf8_for_unknown_charset():
    body = "Ижевск".encode("utf-8")
    text = FastCrawlerRuntime._decode_body(
        body,
        "text/html; charset=definitely-not-a-real-codec",
    )
    assert text == "Ижевск"


def test_fast_runtime_keeps_valid_declared_charset():
    body = "café".encode("latin-1")
    text = FastCrawlerRuntime._decode_body(body, "text/plain; charset=latin-1")
    assert text == "café"
