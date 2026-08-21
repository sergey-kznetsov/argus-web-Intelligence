import pytest

from argus.contracts.models import CollectionRequest
from argus.research.discovery import (
    DiscoveryBlockedError,
    DiscoveryHit,
    DiscoveryService,
)


class FakeGuard:
    async def validate(self, url):
        return url


class FakeProvider:
    def __init__(self, name, *, hits=None, error=None):
        self.name = name
        self.hits = hits or []
        self.error = error
        self.calls = 0

    async def discover(self, queries, request):
        del queries, request
        self.calls += 1
        if self.error:
            raise self.error
        return self.hits

    async def health(self):
        return {"provider": self.name, "status": "ok"}


def request():
    return CollectionRequest(
        consumer="test",
        analysis_id="1",
        territory={"city": "Ижевск"},
        intents=["public_mentions"],
    )


@pytest.mark.asyncio
async def test_discovery_stops_after_first_successful_provider():
    first = FakeProvider(
        "first",
        hits=[DiscoveryHit(url="https://example.com/a", provider="first")],
    )
    second = FakeProvider(
        "second",
        hits=[DiscoveryHit(url="https://example.org/b", provider="second")],
    )
    outcome = await DiscoveryService([first, second], FakeGuard()).discover(
        ["query"], request()
    )
    assert [task.url for task in outcome.tasks] == ["https://example.com/a"]
    assert first.calls == 1
    assert second.calls == 0
    assert outcome.providers_attempted == ["first"]


@pytest.mark.asyncio
async def test_discovery_uses_next_provider_after_empty_result():
    first = FakeProvider("first")
    second = FakeProvider(
        "second",
        hits=[DiscoveryHit(url="https://example.org/b", provider="second")],
    )
    outcome = await DiscoveryService([first, second], FakeGuard()).discover(
        ["query"], request()
    )
    assert [task.url for task in outcome.tasks] == ["https://example.org/b"]
    assert outcome.providers_attempted == ["first", "second"]


@pytest.mark.asyncio
async def test_blocked_provider_is_recorded_and_fallback_can_continue():
    first = FakeProvider(
        "first",
        error=DiscoveryBlockedError("search challenge"),
    )
    second = FakeProvider(
        "second",
        hits=[DiscoveryHit(url="https://example.org/b", provider="second")],
    )
    outcome = await DiscoveryService([first, second], FakeGuard()).discover(
        ["query"], request()
    )
    assert outcome.blocked is True
    assert outcome.errors[0].code == "DISCOVERY_BLOCKED"
    assert [task.url for task in outcome.tasks] == ["https://example.org/b"]
    assert not any(error.code == "DISCOVERY_INCOMPLETE" for error in outcome.errors)


@pytest.mark.asyncio
async def test_all_empty_providers_report_no_results():
    first = FakeProvider("first")
    second = FakeProvider("second")
    outcome = await DiscoveryService([first, second], FakeGuard()).discover(
        ["query"], request()
    )
    assert outcome.tasks == []
    assert outcome.providers_attempted == ["first", "second"]
    assert [error.code for error in outcome.errors] == ["DISCOVERY_NO_RESULTS"]
    assert outcome.blocked is False


@pytest.mark.asyncio
async def test_fully_blocked_discovery_is_explicitly_incomplete():
    provider = FakeProvider(
        "blocked",
        error=DiscoveryBlockedError("search challenge"),
    )
    outcome = await DiscoveryService([provider], FakeGuard()).discover(
        ["query"], request()
    )
    assert outcome.tasks == []
    assert outcome.blocked is True
    assert [error.code for error in outcome.errors] == [
        "DISCOVERY_BLOCKED",
        "DISCOVERY_INCOMPLETE",
    ]
