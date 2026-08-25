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


class CanonicalProvider:
    name = "canonical"

    async def discover(self, queries, request):
        del queries, request
        return [
            DiscoveryHit(
                url="https://Example.com:443/a?utm_source=x&id=1#section",
                provider=self.name,
                title="Ижевск объект",
                rank=2,
            ),
            DiscoveryHit(
                url="https://example.com/a?id=1",
                provider=self.name,
                title="duplicate",
                rank=3,
            ),
            DiscoveryHit(
                url="https://example.com/a?id=2",
                provider=self.name,
                title="other",
                rank=1,
            ),
        ]

    async def health(self):
        return {"status": "ok"}


class RankingProvider:
    name = "ranking"

    async def discover(self, queries, request):
        del queries, request
        return [
            DiscoveryHit(
                url="http://secondary.test/general",
                provider=self.name,
                title="general",
                rank=1,
            ),
            DiscoveryHit(
                url="https://priority.test/news/izhevsk",
                provider=self.name,
                title="Ижевск новость",
                rank=20,
            ),
            DiscoveryHit(
                url="https://secondary.test/local/izhevsk",
                provider=self.name,
                title="Ижевск материал",
                rank=2,
            ),
            DiscoveryHit(
                url="https://secondary.test/other",
                provider=self.name,
                title="other",
                rank=2,
            ),
        ]

    async def health(self):
        return {"status": "ok"}


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
    assert task.metadata["discovery_original_url"] == "http://localhost/article"
    assert task.metadata["discovery_canonical_url"] == "http://localhost/article"
    assert task.metadata["discovery_ranking_version"] == "discovery-ranking/1"


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


@pytest.mark.asyncio
async def test_tracking_fragment_and_default_port_collapse_to_one_destination():
    request = CollectionRequest(
        consumer="test",
        analysis_id="canonical",
        territory={"city": "Ижевск"},
        intents=["public_mentions"],
    )
    service = DiscoveryService(
        providers=[CanonicalProvider()],
        url_guard=UrlGuard.from_strings(["example.com"]),
    )

    outcome = await service.discover(["query"], request)

    urls = [task.url for task in outcome.tasks]
    assert urls == ["https://example.com/a?id=2", "https://example.com/a?id=1"]
    second = outcome.tasks[1]
    assert second.metadata["discovery_original_url"].startswith("https://Example.com:443/")
    assert second.metadata["discovery_canonical_url"] == "https://example.com/a?id=1"
    assert second.metadata["discovery_locality_matches"] == 1


@pytest.mark.asyncio
async def test_allowed_domain_order_is_explicit_priority_before_provider_rank():
    request = CollectionRequest(
        consumer="test",
        analysis_id="ranking",
        territory={"city": "Ижевск"},
        intents=["local_news"],
        constraints={
            "allowed_domains": ["priority.test", "secondary.test"],
            "max_pages": 10,
        },
    )
    service = DiscoveryService(
        providers=[RankingProvider()],
        url_guard=UrlGuard.from_strings(["priority.test", "secondary.test"]),
    )

    outcome = await service.discover(["query"], request)

    assert [task.url for task in outcome.tasks] == [
        "https://priority.test/news/izhevsk",
        "http://secondary.test/general",
        "https://secondary.test/local/izhevsk",
        "https://secondary.test/other",
    ]
    assert outcome.tasks[0].metadata["discovery_domain_priority"] == 0
    assert outcome.tasks[1].metadata["discovery_domain_priority"] == 1


@pytest.mark.asyncio
async def test_locality_and_https_break_equal_rank_ties_deterministically():
    request = CollectionRequest(
        consumer="test",
        analysis_id="tie",
        territory={"city": "Ижевск"},
        intents=["local_news"],
        constraints={"allowed_domains": ["secondary.test"]},
    )
    service = DiscoveryService(
        providers=[RankingProvider()],
        url_guard=UrlGuard.from_strings(["secondary.test"]),
    )

    outcome = await service.discover(["query"], request)

    urls = [task.url for task in outcome.tasks]
    assert urls[0] == "http://secondary.test/general"
    assert urls[1:3] == [
        "https://secondary.test/local/izhevsk",
        "https://secondary.test/other",
    ]
    assert outcome.tasks[1].metadata["discovery_locality_matches"] == 1
    assert outcome.tasks[1].metadata["discovery_https"] is True


@pytest.mark.asyncio
async def test_destination_count_is_bounded_by_request_max_pages():
    request = CollectionRequest(
        consumer="test",
        analysis_id="limit",
        territory={"city": "Ижевск"},
        intents=["public_mentions"],
        constraints={"max_pages": 1},
    )
    service = DiscoveryService(
        providers=[CanonicalProvider()],
        url_guard=UrlGuard.from_strings(["example.com"]),
    )

    outcome = await service.discover(["query"], request)

    assert len(outcome.tasks) == 1
    assert outcome.tasks[0].url == "https://example.com/a?id=2"
