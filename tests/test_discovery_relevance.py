import pytest

from argus.contracts.models import CollectionRequest
from argus.research.discovery import DiscoveryHit
from argus.research.discovery_relevance import TerritoryAwareDiscoveryService


class FakeGuard:
    async def validate(self, url):
        return url


class FakeProvider:
    name = "fake"

    def __init__(self, hits):
        self.hits = hits

    async def discover(self, queries, request):
        del queries, request
        return list(self.hits)

    async def health(self):
        return {"status": "ok"}


def request() -> CollectionRequest:
    return CollectionRequest(
        consumer="test",
        analysis_id="discovery-relevance",
        territory={"city": "Пермь", "address": "Комсомольский проспект, 27"},
        intents=["reviews"],
        constraints={"max_pages": 10},
    )


@pytest.mark.asyncio
async def test_local_result_outranks_higher_ranked_unrelated_documentation():
    query = '"Пермь, Комсомольский проспект, 27" отзывы'
    provider = FakeProvider(
        [
            DiscoveryHit(
                url="https://learn.example/query-overview",
                provider="fake",
                title="Query overview documentation",
                rank=1,
                query=query,
            ),
            DiscoveryHit(
                url="https://2gis.ru/perm/search/Комсомольский%20проспект%2027",
                provider="fake",
                title="Комсомольский проспект 27 в Перми — отзывы",
                rank=7,
                query=query,
            ),
        ]
    )
    discovery = TerritoryAwareDiscoveryService([provider], FakeGuard())

    outcome = await discovery.discover([query], request())

    assert outcome.tasks[0].url.startswith("https://2gis.ru/perm/")
    assert outcome.tasks[0].metadata["discovery_ranking_version"] == "discovery-ranking/2"


@pytest.mark.asyncio
async def test_positive_batch_keeps_only_one_zero_signal_fallback():
    query = '"Пермь, Комсомольский проспект, 27" отзывы'
    provider = FakeProvider(
        [
            DiscoveryHit(
                url="https://2gis.ru/perm/search/address",
                provider="fake",
                title="Комсомольский проспект 27 Пермь",
                rank=5,
                query=query,
            ),
            DiscoveryHit(
                url="https://docs.example/sql",
                provider="fake",
                title="SQL syntax",
                rank=1,
                query=query,
            ),
            DiscoveryHit(
                url="https://docs.example/power-query",
                provider="fake",
                title="Power Query overview",
                rank=2,
                query=query,
            ),
            DiscoveryHit(
                url="https://docs.example/bigquery",
                provider="fake",
                title="BigQuery introduction",
                rank=3,
                query=query,
            ),
        ]
    )
    discovery = TerritoryAwareDiscoveryService([provider], FakeGuard())

    outcome = await discovery.discover([query], request())

    assert len(outcome.tasks) == 2
    assert outcome.tasks[0].url.startswith("https://2gis.ru/")
    assert sum(task.url.startswith("https://docs.example/") for task in outcome.tasks) == 1


@pytest.mark.asyncio
async def test_all_neutral_batch_is_preserved_to_avoid_false_negative_filtering():
    query = '"Пермь, Комсомольский проспект, 27" история'
    provider = FakeProvider(
        [
            DiscoveryHit(
                url="https://archive.example/item-a",
                provider="fake",
                title="Гостиница Прикамье",
                rank=1,
                query=query,
            ),
            DiscoveryHit(
                url="https://archive.example/item-b",
                provider="fake",
                title="Историческая фотография центра города",
                rank=2,
                query=query,
            ),
        ]
    )
    discovery = TerritoryAwareDiscoveryService([provider], FakeGuard())

    outcome = await discovery.discover([query], request())

    assert len(outcome.tasks) == 2
