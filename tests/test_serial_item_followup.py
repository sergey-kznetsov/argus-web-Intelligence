from __future__ import annotations

import pytest

from argus.contracts.models import CollectionRequest
from argus.crawler.models import FetchResult
from argus.orchestrator.mandatory_coverage import MandatoryCoverageToolPackOrchestrator
from argus.orchestrator.toolpack_aware import (
    ToolPackAwareEvidenceStatusAdaptiveResearchOrchestrator,
)
from argus.sources.atomic_content_web import AtomicContentWebAdapter
from argus.sources.base import SourceResult, SourceTask
from argus.sources.intent_evidence_web import IntentEvidenceWebAdapter


def _request() -> CollectionRequest:
    return CollectionRequest(
        consumer="kraken.development.uds",
        consumer_profile_version=1,
        capability="urban_signals",
        analysis_id="serial-item-followup",
        territory={"city": "Ижевск", "address": "Пушкинская улица, 277"},
        intents=["local_news", "complaints"],
        constraints={"max_pages": 500, "max_depth": 2},
    )


def _contour_task(url: str) -> SourceTask:
    return SourceTask(
        source_id="generic_web",
        goal="local_news",
        url=url,
        depth=0,
        metadata={
            "research_goals": ["local_news", "complaints"],
            "source_contour": "local_media",
            "source_contour_version": "source-contours/5",
        },
    )


def test_source_contour_listing_selects_one_terminal_item_before_feed() -> None:
    adapter = object.__new__(AtomicContentWebAdapter)
    adapter.sitemap_discovery_enabled = False
    fetched = FetchResult(
        url="https://example.org/news/",
        final_url="https://example.org/news/",
        status_code=200,
        content_type="text/html",
        text=(
            '<html><head><link rel="alternate" type="application/rss+xml" '
            'href="/feed.xml"></head><body>listing</body></html>'
        ),
        links=[
            "https://example.org/login",
            "https://example.org/news/2026/09/09/water-main-break-pushkinskaya",
            "https://example.org/topic/12345",
        ],
    )

    discovered = adapter._discovered_tasks(
        _contour_task(fetched.final_url),
        fetched,
        _request(),
        "collection-followup",
    )

    assert len(discovered) == 1
    selected = discovered[0]
    assert selected.source_id == "generic_web"
    assert selected.url == (
        "https://example.org/news/2026/09/09/water-main-break-pushkinskaya"
    )
    assert selected.metadata["serial_item_followup_terminal"] is True
    assert selected.metadata["serial_item_followup_policy"] == "serial-item-followup/2"
    assert selected.metadata["serial_item_followup_kind"] == "item"
    assert selected.metadata["serial_item_followup_parent_url"] == fetched.final_url
    assert selected.metadata["serial_item_followup_navigation_reason"] == (
        "url_navigation_shell"
    )


def test_semantic_article_cards_turn_opaque_city_section_into_listing() -> None:
    adapter = object.__new__(AtomicContentWebAdapter)
    adapter.sitemap_discovery_enabled = False
    fetched = FetchResult(
        url="https://example.org/izhevsk/",
        final_url="https://example.org/izhevsk/",
        status_code=200,
        content_type="text/html",
        text=(
            "<html><body>"
            '<article><h2><a href="/news/2026/09/09/pushkinskaya-water">'
            "Авария на Пушкинской</a></h2><p>Краткая карточка новости.</p></article>"
            '<article><h2><a href="/news/2026/09/08/road-repair">'
            "Ремонт дороги</a></h2><p>Краткая карточка новости.</p></article>"
            "</body></html>"
        ),
        links=[
            "https://example.org/news/2026/09/09/pushkinskaya-water",
            "https://example.org/news/2026/09/08/road-repair",
            "https://example.org/weather",
        ],
    )

    discovered = adapter._discovered_tasks(
        _contour_task(fetched.final_url),
        fetched,
        _request(),
        "collection-opaque-listing",
    )

    assert len(discovered) == 1
    selected = discovered[0]
    assert selected.url == "https://example.org/news/2026/09/09/pushkinskaya-water"
    assert selected.metadata["serial_item_followup_terminal"] is True
    assert selected.metadata["serial_item_followup_navigation_reason"] == (
        "linked_article_listing_cards"
    )
    assert selected.metadata["content_navigation_score"] > 0


def test_source_contour_item_entry_does_not_fan_out_again() -> None:
    adapter = object.__new__(AtomicContentWebAdapter)
    adapter.sitemap_discovery_enabled = False
    item_url = "https://example.org/news/2026/09/09/water-main-break-pushkinskaya"
    fetched = FetchResult(
        url=item_url,
        final_url=item_url,
        status_code=200,
        content_type="text/html",
        text="<html><body>article</body></html>",
        links=[
            "https://example.org/news/2026/09/10/next-story",
            "https://example.org/news/",
        ],
    )
    task = _contour_task(item_url)

    discovered = adapter._discovered_tasks(
        task,
        fetched,
        _request(),
        "collection-item-entry",
    )

    assert discovered == []
    assert task.metadata["serial_item_followup_policy"] == "serial-item-followup/2"
    assert task.metadata["serial_item_followup_reason"] == "entry_is_item"


def test_terminal_item_followup_never_creates_grandchildren() -> None:
    adapter = object.__new__(AtomicContentWebAdapter)
    adapter.sitemap_discovery_enabled = False
    item_url = "https://example.org/topic/12345"
    task = SourceTask(
        source_id="generic_web",
        goal="complaints",
        url=item_url,
        depth=1,
        metadata={
            "source_contour": "local_forums",
            "serial_item_followup_terminal": True,
        },
    )
    fetched = FetchResult(
        url=item_url,
        final_url=item_url,
        status_code=200,
        content_type="text/html",
        text="<html><body>topic</body></html>",
        links=["https://example.org/topic/67890"],
    )

    assert (
        adapter._discovered_tasks(
            task,
            fetched,
            _request(),
            "collection-terminal",
        )
        == []
    )


@pytest.mark.asyncio
async def test_listing_followup_becomes_atomic_publication(monkeypatch) -> None:
    adapter = object.__new__(AtomicContentWebAdapter)
    adapter.sitemap_discovery_enabled = False
    request = _request()
    listing = FetchResult(
        url="https://example.org/news/",
        final_url="https://example.org/news/",
        status_code=200,
        content_type="text/html",
        text="<html><body>listing</body></html>",
        links=[
            "https://example.org/news/2026/09/09/water-main-break-pushkinskaya",
            "https://example.org/news/",
        ],
    )

    selected = adapter._discovered_tasks(
        _contour_task(listing.final_url),
        listing,
        request,
        "collection-atomic-flow",
    )[0]
    selected.metadata["collection_id"] = "collection-atomic-flow"
    selected.depth = 1

    async def fake_parent_extract(self, task, fetched, request):
        del self, task, fetched, request
        return SourceResult(observations=[])

    monkeypatch.setattr(
        IntentEvidenceWebAdapter,
        "extract",
        fake_parent_extract,
    )
    item = FetchResult(
        url=selected.url,
        final_url=selected.url,
        status_code=200,
        content_type="text/html",
        text=(
            "<html><body><article><h1>Авария на Пушкинской улице</h1>"
            "<p>Жители сообщили о повреждении водопровода на Пушкинской улице. "
            "Коммунальная служба огородила участок и начала ремонтные работы.</p>"
            "<p>В публикации указано, что восстановление подачи воды ожидается "
            "после завершения аварийных работ в этом квартале.</p>"
            "</article></body></html>"
        ),
        links=[],
    )

    result = await adapter.extract(selected, item, request)

    publications = [
        observation
        for observation in result.observations
        if observation.entity_type == "publication"
    ]
    assert len(publications) == 1
    publication = publications[0]
    assert publication.source_kind == "html_atomic"
    assert publication.title == "Авария на Пушкинской улице"
    assert "повреждении водопровода" in (publication.text or "")
    assert publication.quality["atomic_content"] is True
    assert publication.quality["whole_page_document"] is False
    assert len(result.evidence) == 1
    assert result.evidence[0].observation_id == publication.observation_id
    assert selected.metadata["serial_item_followup_terminal"] is True


@pytest.mark.asyncio
async def test_mandatory_source_contour_reserves_two_pages_per_entry(
    monkeypatch,
) -> None:
    captured: list[tuple[str, int]] = []

    async def fake_base_process(
        self,
        record,
        lane_tasks,
        deferred_pending,
        *,
        lane_id,
        lane_kind,
        lane_label,
        page_limit,
        future_lane_count,
    ):
        del self, lane_tasks, deferred_pending, lane_id, lane_label, future_lane_count
        captured.append((lane_kind, page_limit))
        return record, {"lane_page_limit": page_limit}

    monkeypatch.setattr(
        ToolPackAwareEvidenceStatusAdaptiveResearchOrchestrator,
        "_process_serial_lane",
        fake_base_process,
    )
    orchestrator = object.__new__(MandatoryCoverageToolPackOrchestrator)
    entries = [
        SourceTask(
            source_id="generic_web",
            goal="local_news",
            url=f"https://example.org/news/page-{index}",
        )
        for index in range(5)
    ]
    record = object()

    _, stats = await orchestrator._process_serial_lane(
        record,
        entries,
        [],
        lane_id="source_contour:local_media",
        lane_kind="source_contour",
        lane_label="local_media",
        page_limit=6,
        future_lane_count=5,
    )

    assert captured == [("source_contour", 10)]
    assert stats["lane_page_limit"] == 10


@pytest.mark.asyncio
async def test_mandatory_public_map_keeps_existing_lane_budget(monkeypatch) -> None:
    captured: list[tuple[str, int]] = []

    async def fake_base_process(
        self,
        record,
        lane_tasks,
        deferred_pending,
        *,
        lane_id,
        lane_kind,
        lane_label,
        page_limit,
        future_lane_count,
    ):
        del self, lane_tasks, deferred_pending, lane_id, lane_label, future_lane_count
        captured.append((lane_kind, page_limit))
        return record, {"lane_page_limit": page_limit}

    monkeypatch.setattr(
        ToolPackAwareEvidenceStatusAdaptiveResearchOrchestrator,
        "_process_serial_lane",
        fake_base_process,
    )
    orchestrator = object.__new__(MandatoryCoverageToolPackOrchestrator)
    entries = [
        SourceTask(
            source_id="generic_web",
            goal="comments",
            url="https://2gis.ru/izhevsk/search/test",
        )
    ]

    await orchestrator._process_serial_lane(
        object(),
        entries,
        [],
        lane_id="public_map:2gis_web",
        lane_kind="public_map",
        lane_label="2gis_web",
        page_limit=4,
        future_lane_count=2,
    )

    assert captured == [("public_map", 4)]
