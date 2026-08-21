from argus.security.redaction import redact_text, redact_url, safe_error_message


def test_redact_url_removes_userinfo_query_and_fragment():
    value = redact_url("https://user:pass@example.com/path?token=secret#fragment")
    assert value == "https://example.com/path"


def test_redact_text_removes_common_secrets_and_query_parameters():
    raw = (
        "Authorization=Bearer abc.def token=mytoken password=hunter2 "
        "GET https://example.com/path?api_key=supersecret"
    )
    safe = redact_text(raw)

    for secret in ("abc.def", "mytoken", "hunter2", "supersecret"):
        assert secret not in safe
    assert "https://example.com/path" in safe
    assert "?" not in safe


def test_safe_error_message_does_not_persist_secret_url():
    error = RuntimeError("failed https://example.com/data?access_token=secret-value")
    safe = safe_error_message(error)

    assert "secret-value" not in safe
    assert safe == "failed https://example.com/data"
