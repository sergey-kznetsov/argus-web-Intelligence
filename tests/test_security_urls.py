import pytest

from argus.security.urls import UnsafeUrlError, UrlGuard


@pytest.mark.asyncio
async def test_blocks_loopback_literal():
    guard = UrlGuard.from_strings([])
    with pytest.raises(UnsafeUrlError):
        await guard.validate("http://127.0.0.1/admin")


@pytest.mark.asyncio
async def test_explicit_allowlist_allows_internal_literal_and_custom_port():
    guard = UrlGuard.from_strings(["127.0.0.1"])
    assert await guard.validate("http://127.0.0.1:9999/")


@pytest.mark.asyncio
async def test_denylist_wins_over_internal_allowlist():
    guard = UrlGuard.from_strings(
        ["internal.example"],
        deny_values=["internal.example"],
    )
    with pytest.raises(UnsafeUrlError, match="denied"):
        await guard.validate("https://internal.example/")


@pytest.mark.asyncio
async def test_denylist_blocks_subdomains_without_dns_lookup():
    guard = UrlGuard.from_strings([], deny_values=["example.com"])
    with pytest.raises(UnsafeUrlError, match="denied"):
        await guard.validate("https://private.example.com/data")


@pytest.mark.asyncio
async def test_blocks_public_target_port_outside_policy(monkeypatch):
    async def public_resolve(host: str, port: int) -> set[str]:
        del host, port
        return {"93.184.216.34"}

    monkeypatch.setattr(UrlGuard, "_resolve", staticmethod(public_resolve))
    guard = UrlGuard.from_strings([], public_ports=[80, 443])
    with pytest.raises(UnsafeUrlError, match="port"):
        await guard.validate("https://example.com:8443/")


@pytest.mark.asyncio
async def test_allows_configured_public_target_port(monkeypatch):
    async def public_resolve(host: str, port: int) -> set[str]:
        assert host == "example.com"
        assert port == 8443
        return {"93.184.216.34"}

    monkeypatch.setattr(UrlGuard, "_resolve", staticmethod(public_resolve))
    guard = UrlGuard.from_strings([], public_ports=[443, 8443])
    assert await guard.validate("https://example.com:8443/")


@pytest.mark.asyncio
async def test_redirect_rechecks_outbound_policy():
    guard = UrlGuard.from_strings([], deny_values=["blocked.example"])
    with pytest.raises(UnsafeUrlError, match="denied"):
        await guard.validate_redirect(
            "https://allowed.example/",
            "https://sub.blocked.example/path",
        )


@pytest.mark.asyncio
async def test_blocks_invalid_port():
    guard = UrlGuard.from_strings([])
    with pytest.raises(UnsafeUrlError, match="port"):
        await guard.validate("https://example.com:99999/")


def test_rejects_invalid_public_port_configuration():
    with pytest.raises(ValueError, match="public_ports"):
        UrlGuard.from_strings([], public_ports=[0])


@pytest.mark.asyncio
async def test_blocks_non_http():
    guard = UrlGuard.from_strings([])
    with pytest.raises(UnsafeUrlError):
        await guard.validate("file:///etc/passwd")
