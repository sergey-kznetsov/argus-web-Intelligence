import pytest

from argus.security.urls import UnsafeUrlError, UrlGuard


@pytest.mark.asyncio
async def test_blocks_loopback_literal():
    guard = UrlGuard.from_strings([])
    with pytest.raises(UnsafeUrlError):
        await guard.validate("http://127.0.0.1/admin")


@pytest.mark.asyncio
async def test_explicit_allowlist_allows_internal_literal():
    guard = UrlGuard.from_strings(["127.0.0.1"])
    assert await guard.validate("http://127.0.0.1:9999/")


@pytest.mark.asyncio
async def test_blocks_non_http():
    guard = UrlGuard.from_strings([])
    with pytest.raises(UnsafeUrlError):
        await guard.validate("file:///etc/passwd")
