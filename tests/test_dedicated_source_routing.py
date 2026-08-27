from __future__ import annotations

import pytest

from argus.contracts.models import CollectionRequest
from argus.research.discovery import DiscoveryHit
from argus.research.source_routing import DedicatedSourceRoutingDiscoveryService


class _Guard:
    async def validate(self, url: str) -> None:
        return None


class _Provider:
    name = "test-search"

    async def discover(self, queries, request):
        return [
            DiscoveryHit(
                url="https://dom.mingkh.ru/perm/perm/123456",
                provider=self.name,
                title="Комсомольский проспект, 27 — многоквартирный дом",
                rank=1,
                query=queries[0],
            ),
            DiscoveryHit(
                url="https://example.org/perm-house-27",
                provider=self.name,
                title="Комсомольский проспект 27",
                rank=2,
                query=queries[0],
            ),
        ]

    async def health(self):
        return {"status": "ok"}


def _request(*intents: str) -> CollectionRequest:
    return CollectionRequest(
        consumer="routing-test",
        analysis_id="a1",
        territory={"city": "Пермь", "address": "Комсомольский проспект, 27"},
        intents=list(intents),
    )


@pytest.mark.asyncio
async def test_residential_only_discovery_is_fail_closed_to_mingkh():
    discovery = DedicatedSourceRoutingDiscoveryService(
        providers=[_Provider()],
        url_guard=_Guard(),
        max_queries=4,
        domain_source_routes={"dom.mingkh.ru": "mingkh_residential"},
    )
    outcome = await discovery.discover(
        ['site:dom.mingkh.ru "Пермь, Комсомольский проспект, 27" "Количество квартир"'],
        _request("residential_population", "residential_premises_count"),
    )

    assert len(outcome.tasks) == 1
    task = outcome.tasks[0]
    assert task.source_id == "mingkh_residential"
    assert task.url.startswith("https://dom.mingkh.ru/")
    assert task.metadata["dedicated_source_route"]["is_evidence"] is False
    assert task.metadata["research_input_candidates"] == [
        "Пермь, Комсомольский проспект, 27",
        "Комсомольский проспект, 27",
        "Пермь",
    ]
    assert task.metadata["research_input_scope"] == "territory_context"
    assert task.metadata["allowed_domains"] == ["dom.mingkh.ru"]
    joined_inputs = " ".join(task.metadata["research_input_candidates"])
    assert "site:dom.mingkh.ru" not in joined_inputs
    assert "Количество квартир" not in joined_inputs


@pytest.mark.asyncio
async def test_mixed_request_keeps_normal_sources_for_other_intents():
    discovery = DedicatedSourceRoutingDiscoveryService(
        providers=[_Provider()],
        url_guard=_Guard(),
        max_queries=4,
        domain_source_routes={"dom.mingkh.ru": "mingkh_residential"},
    )
    outcome = await discovery.discover(
        ['"Пермь, Комсомольский проспект, 27" новости'],
        _request("residential_population", "local_news"),
    )

    assert {task.source_id for task in outcome.tasks} == {
        "mingkh_residential",
        "generic_web",
    }
    mingkh = next(task for task in outcome.tasks if task.source_id == "mingkh_residential")
    assert mingkh.metadata["research_input_scope"] == "territory_context"
    assert mingkh.metadata["research_input_candidates"] == [
        "Пермь, Комсомольский проспект, 27",
        "Комсомольский проспект, 27",
        "Пермь",
    ]
