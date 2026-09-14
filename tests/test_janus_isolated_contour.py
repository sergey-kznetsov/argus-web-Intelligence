from __future__ import annotations

import pytest

from argus.contracts.models import CollectionConstraints, CollectionRequest, TerritoryContext
from argus.research.residential_sources import (
    CuratedResidentialFollowupResearchPlanner,
    CuratedResidentialResearchPlanner,
    MingkhResidentialSourceResearchPlanner,
)
from argus.toolpacks import TOOL_PACK_REGISTRY


class _ForbiddenPlanner:
    async def plan(self, _request):
        raise AssertionError("Janus must not delegate to the generic research planner")


class _ForbiddenFollowupPlanner:
    async def plan_followups(self, *_args, **_kwargs):
        raise AssertionError("Janus must not delegate to generic follow-up research")


def _request(
    *,
    intents: list[str] | None = None,
    constraints: CollectionConstraints | None = None,
) -> CollectionRequest:
    return CollectionRequest(
        consumer="janus.parking.potential.uds",
        consumer_profile_version=1,
        capability="residential_facts",
        requested_facts=["residential_premises_count"],
        analysis_id="janus-isolated-test",
        territory=TerritoryContext(
            city="Ижевск",
            address="Ижевск, Пушкинская улица, 115",
        ),
        intents=intents or ["residential_premises_count"],
        constraints=constraints or CollectionConstraints(max_pages=18, max_depth=2),
    )


def test_janus_request_is_clamped_to_single_site_single_pass() -> None:
    request = _request()
    assert request.tool_pack_id == "janus.residential_facts"
    assert request.constraints.allowed_domains == ["dom.mingkh.ru"]
    assert request.constraints.max_pages == 1
    assert request.constraints.max_depth == 0


def test_janus_rejects_intents_outside_residential_premises_fact() -> None:
    with pytest.raises(ValueError, match="TOOL_PACK_INTENT_SCOPE"):
        _request(intents=["residential_population"])
    with pytest.raises(ValueError, match="TOOL_PACK_INTENT_SCOPE"):
        _request(intents=["parking_capacity"])


@pytest.mark.parametrize(
    "constraints",
    [
        CollectionConstraints(allowed_domains=["example.com"]),
        CollectionConstraints(seed_urls=["https://example.com/house/1"]),
        CollectionConstraints(source_pool_urls=["https://example.com/search"]),
        CollectionConstraints(denied_domains=["dom.mingkh.ru"]),
        CollectionConstraints(denied_domains=["mingkh.ru"]),
    ],
)
def test_janus_rejects_network_scope_outside_or_against_mingkh(
    constraints: CollectionConstraints,
) -> None:
    with pytest.raises(ValueError, match="TOOL_PACK_DOMAIN_SCOPE"):
        _request(constraints=constraints)


def test_janus_direct_source_planner_creates_only_mingkh_task() -> None:
    request = _request()
    planner = MingkhResidentialSourceResearchPlanner()
    tasks = planner.tasks(request)

    assert planner.queries(request) == []
    assert len(tasks) == 1
    task = tasks[0]
    assert task.source_id == "mingkh_residential"
    assert task.goal == "residential_premises_count"
    assert task.url == "https://dom.mingkh.ru/"
    assert task.metadata["allowed_domains"] == ["dom.mingkh.ru"]
    assert task.metadata["dedicated_source_navigation"] == "address_interface"
    assert task.metadata["janus_isolated_contour"] is True
    assert task.metadata["external_discovery_allowed"] is False


@pytest.mark.asyncio
async def test_janus_initial_plan_never_calls_generic_planner() -> None:
    request = _request()
    planner = CuratedResidentialResearchPlanner(_ForbiddenPlanner())

    plan = await planner.plan(request)

    assert plan.queries == []
    assert [task.source_id for task in plan.tasks] == ["mingkh_residential"]
    assert any("generic_discovery=false" in note for note in plan.notes)
    assert any("analysis=false" in note for note in plan.notes)


@pytest.mark.asyncio
async def test_janus_followup_never_calls_generic_research_or_search() -> None:
    request = _request()
    planner = CuratedResidentialFollowupResearchPlanner(_ForbiddenFollowupPlanner())

    plan = await planner.plan_followups(
        request,
        [],
        seen_queries=set(),
        max_queries=10,
    )

    assert plan.queries == []
    assert any("search_provider=false" in note for note in plan.notes)
    assert any("external_discovery=false" in note for note in plan.notes)


def test_kraken_soika_toolpack_remains_unbounded_by_janus_changes() -> None:
    pack = TOOL_PACK_REGISTRY.get("kraken.urban_signals")
    assert pack is not None
    assert pack.allowed_source_ids == (
        "generic_web",
        "rss_atom",
        "json_feed",
        "site_discovery",
        "openstreetmap_overpass",
    )
    assert pack.allowed_intents == ()
    assert pack.shared_tools == ("fast", "browser", "evidence", "provenance", "snapshots")
    assert pack.planner_policy == "urban_signals"
    assert pack.result_delivery_policy == "soika_message_stream"
    assert pack.result_dedup_policy == "none"
    assert pack.exclusive_domains == ()
    assert pack.max_pages is None
    assert pack.max_depth is None
