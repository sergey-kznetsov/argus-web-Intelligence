import pytest

from argus.contracts.models import CollectionRequest
from argus.research.discovery import DiscoveryHit, DiscoveryService
from argus.security.urls import UrlGuard


class FakeProvider:
    name = "fake"

    async def discover(self, queries, request):
        del queries, request
        return [
            DiscoveryHit(
                url="http://localhost/article",
                provider=self.name,
                engines=["engine-a"],
                rank=1,
            ),
            DiscoveryHit(
                url="http://localhost/article",
                provider=self.name,
                rank=2,
            ),
            DiscoveryHit(
                url="http://127.0.0.1/secret",
                provider=self.name,
                rank=3,
            ),
        ]

    async def health(self):
        return {"status": "ok"}


class FailingProvider:
    name = "broken"

    async def discover(self, queries, request):
        del queries, request
        raise RuntimeError("failed https://search.example/?token=secret-value")

    async def health(self):
        return {"status": "unavailable"}


@pytest.mark.asyncio
async def test_discovery_only_seeds_valid_deduplicated_source_urls():
    request = CollectionRequest(
        consumer="test",
        analysis_id="1",
        territory={"city": "Ижевск"},
        intents=["public_mentions"],
    )
    service = DiscoveryService(
        providers=[FakeProvider()],
        url_guard=UrlGuard.from_strings(["localhost"]),
    )

    outcome = await service.discover(["query"], request)

    assert outcome.errors == []
    assert outcome.providers_attempted == ["fake"]
    assert len(outcome.tasks) == 1
    task = outcome.tasks[0]
    assert task.source_id == "generic_web"
    assert task.url == "http://localhost/article"
    assert task.metadata["discovery_provider"] == "fake"
    assert task.metadata["discovery_engines"] == ["engine-a"]


@pytest.mark.asyncio
async def test_historical_discovery_adds_archive_companion_task():
    request = CollectionRequest(
        consumer="test",
        analysis_id="1",
        territory={"city": "Ижевск"},
        intents=["historical_context"],
    )
    service = DiscoveryService(
        providers=[FakeProvider()],
        url_guard=UrlGuard.from_strings(["localhost"]),
        historical_archive_source_id="wayback_cdx",
    )

    outcome = await service.discover(["query"], request)

    assert len(outcome.tasks) == 2
    generic, archive = outcome.tasks
    assert generic.source_id == "generic_web"
    assert archive.source_id == "wayback_cdx"
    assert archive.url == generic.url == "http://localhost/article"
    assert archive.dedupe_key == "wayback_cdx:http://localhost/article"
    assert archive.metadata["archive_target_url"] == "http://localhost/article"


@pytest.mark.asyncio
async def test_discovery_provider_errors_are_redacted():
    request = CollectionRequest(
        consumer="test",
        analysis_id="1",
        territory={"city": "Ижевск"},
        intents=["public_mentions"],
    )
    service = DiscoveryService(
        providers=[FailingProvider()],
        url_guard=UrlGuard.from_strings([]),
    )

    outcome = await service.discover(["query"], request)

    assert outcome.tasks == []
    assert len(outcome.errors) == 1
    assert outcome.errors[0].code == "DISCOVERY_ERROR"
    assert "secret-value" not in outcome.errors[0].message
