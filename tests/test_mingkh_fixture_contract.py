from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.contracts.models import CollectionRequest
from argus.crawler.models import FetchResult
from argus.research.intent_coverage import IntentCoverageEvaluator
from argus.sources.base import SourceTask
from argus.sources.mingkh_residential import MingkhResidentialAdapter


FIXTURES = Path(__file__).with_name("fixtures")


class _Snapshots:
    async def capture(self, *args, **kwargs):
        return SimpleNamespace(snapshot_id="snapshot-fixture")


class _Web:
    def __init__(self) -> None:
        self.navigation_calls = 0

    async def navigate_with_agent(self, task, *, context_fetch):
        del task, context_fetch
        self.navigation_calls += 1
        return None

    async def finalize_navigation_goal(self, task, request, result):
        del task, request, result

    async def health(self):
        return {"status": "ok"}


def _html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _request(address: str = "Комсомольский проспект, 27") -> CollectionRequest:
    return CollectionRequest(
        consumer="janus.parking.potential.uds",
        consumer_profile_version=1,
        capability="residential_facts",
        requested_facts=["residential_premises_count"],
        analysis_id="janus-fixture-analysis",
        territory={"city": "Пермь", "address": address},
        intents=["residential_premises_count"],
        allow_partial=False,
    )


def _task(url: str = "https://dom.mingkh.ru/perm/perm/123456") -> SourceTask:
    return SourceTask(
        source_id="mingkh_residential",
        goal="residential_premises_count",
        url=url,
        metadata={
            "collection_id": "collection-fixture",
            "analysis_id": "janus-fixture-analysis",
        },
    )


def _fetched(html: str, *, url: str, links: list[str] | None = None) -> FetchResult:
    return FetchResult(
        url=url,
        final_url=url,
        status_code=200,
        content_type="text/html; charset=utf-8",
        text=html,
        title="Дом",
        links=list(links or []),
        blocked=False,
        runtime="browser",
        metadata={},
    )


@pytest.mark.parametrize(
    ("fixture_name", "expected_label"),
    [
        ("mingkh_residential_rooms.html", "Жилых помещений"),
        ("mingkh_residential_apartments.html", "Количество квартир"),
    ],
)
def test_known_mingkh_residential_labels_are_parsed_from_fixtures(
    fixture_name: str,
    expected_label: str,
) -> None:
    _text, chunks = MingkhResidentialAdapter._visible_text(_html(fixture_name))
    matches = MingkhResidentialAdapter._extract_values(
        chunks,
        MingkhResidentialAdapter._LABELS["residential_premises_count"],
    )
    assert (expected_label, 32) in matches


def test_mingkh_captcha_fixture_is_detected_before_fact_extraction() -> None:
    visible, _chunks = MingkhResidentialAdapter._visible_text(_html("mingkh_captcha_text.html"))
    assert MingkhResidentialAdapter._has_access_challenge(visible) is True


@pytest.mark.asyncio
async def test_similar_wrong_address_never_becomes_residential_evidence() -> None:
    web = _Web()
    adapter = MingkhResidentialAdapter(web, _Snapshots())
    url = "https://dom.mingkh.ru/perm/perm/123457"
    result = await adapter.extract(
        _task(url),
        _fetched(_html("mingkh_wrong_address.html"), url=url),
        _request(),
    )
    assert result.observations == []
    assert result.evidence == []
    assert [item.code for item in result.errors] == ["MINGKH_TERRITORY_MISMATCH"]
    assert web.navigation_calls == 0


@pytest.mark.asyncio
async def test_janus_single_pass_search_page_does_not_enqueue_similar_house_links() -> None:
    web = _Web()
    adapter = MingkhResidentialAdapter(web, _Snapshots())
    url = "https://dom.mingkh.ru/search"
    links = [
        "https://dom.mingkh.ru/perm/perm/123456",
        "https://dom.mingkh.ru/perm/perm/123457",
    ]
    result = await adapter.extract(
        _task(url),
        _fetched(_html("mingkh_search_similar_addresses.html"), url=url, links=links),
        _request(),
    )
    assert result.observations == []
    assert result.discovered_tasks == []
    # With the fixture request's default depth budget, linked-house follow-up is blocked;
    # the dedicated source may still perform one bounded interface-navigation attempt.
    # Any returned page is re-validated against the exact requested territory.
    assert web.navigation_calls == 1


@pytest.mark.asyncio
async def test_nezhilye_area_text_does_not_imply_non_residential_object_status() -> None:
    web = _Web()
    adapter = MingkhResidentialAdapter(web, _Snapshots())
    url = "https://dom.mingkh.ru/perm/perm/123456"
    result = await adapter.extract(
        _task(url),
        _fetched(_html("mingkh_no_residential_status.html"), url=url),
        _request(),
    )
    statuses = [
        item.data.get("object_status")
        for item in result.observations
        if isinstance(item.data, dict)
    ]
    assert "non_residential" not in statuses


@pytest.mark.asyncio
async def test_explicit_source_label_can_resolve_non_residential_without_fabricating_zero() -> None:
    web = _Web()
    adapter = MingkhResidentialAdapter(web, _Snapshots())
    url = "https://dom.mingkh.ru/perm/perm/123456"
    request = _request()
    result = await adapter.extract(
        _task(url),
        _fetched(_html("mingkh_explicit_non_residential.html"), url=url),
        request,
    )

    assert result.partial is False
    assert result.errors == []
    assert len(result.observations) == 1
    observation = result.observations[0]
    assert observation.source == "mingkh_residential"
    assert observation.data["intent"] == "residential_premises_count"
    assert observation.data["object_status"] == "non_residential"
    assert observation.data["value"] is None
    assert observation.data["source_label"] == "Тип объекта"
    assert observation.data["source_value"] == "Нежилое здание"
    assert observation.quality["not_applicable"] is True
    assert observation.provenance["snapshot_id"] == "snapshot-fixture"
    assert result.evidence[0].text == "Тип объекта: Нежилое здание"
    assert web.navigation_calls == 0

    coverage = IntentCoverageEvaluator()
    assert coverage.supports(
        observation,
        "residential_premises_count",
        request=request,
    ) is True
