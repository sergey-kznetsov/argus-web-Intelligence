from __future__ import annotations

import pytest

from argus.contracts.models import CollectionRequest
from argus.crawler.models import FetchResult
from argus.orchestrator.mandatory_coverage import MandatoryCoverageToolPackOrchestrator
from argus.orchestrator.toolpack_aware import (
    ToolPackAwareEvidenceStatusAdaptiveResearchOrchestrator,
)
from argus.sources.atomic_content_web import AtomicContentWebAdapter
from argus.sources.base import SourceTask


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
    assert selected.metadata["serial_item_followup_policy"] == "serial-item-followup/1"
    assert selected.metadata["serial_item_followup_kind"] == "item"
    assert selected.metadata["serial_item_followup_parent_url"] == fetched.final_url


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
    assert task.metadata["serial_item_followup_policy"] == "serial-item-followup/1"
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
