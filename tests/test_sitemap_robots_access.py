from __future__ import annotations

import asyncio

import pytest

from argus.config import Settings
from argus.contracts.models import CollectionRequest
from argus.sources.base import SourceTask
from argus.sources.sitemap import SitemapDiscoveryAdapter


class _SlowFast:
    async def fetch(self, url: str):
        del url
        await asyncio.sleep(1)
        raise AssertionError("timeout wrapper should stop this request")


class _StatusError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


class _FailingFast:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code

    async def fetch(self, url: str):
        del url
        raise _StatusError(self.status_code)


def _task() -> SourceTask:
    return SourceTask(
        source_id="site_discovery",
        goal="residential_population",
        url="https://dom.mingkh.ru/robots.txt",
        metadata={
            "site_discovery_kind": "robots",
            "root_host": "dom.mingkh.ru",
            "root_origin": "https://dom.mingkh.ru",
        },
    )


def _request() -> CollectionRequest:
    return CollectionRequest(
        consumer="robots-test",
        analysis_id="robots-test",
        territory={"city": "Пермь", "address": "Комсомольский проспект, 27"},
        intents=["residential_population"],
    )


@pytest.mark.asyncio
async def test_network_timeout_stops_source_instead_of_skipping_robots_rules():
    source = SitemapDiscoveryAdapter(Settings(), fast=_SlowFast())
    source.robots_timeout_seconds = 0.01

    fetched = await source.fetch(_task())
    result = await source.extract(_task(), fetched, _request())

    assert fetched is not None
    assert fetched.metadata["robots_access_state"] == "unreachable"
    assert result.blocked is True
    assert result.discovered_tasks == []
    assert [error.code for error in result.errors] == ["SOURCE_ROBOTS_UNREACHABLE"]


@pytest.mark.asyncio
async def test_robots_404_can_continue_to_default_same_host_sitemap():
    source = SitemapDiscoveryAdapter(Settings(), fast=_FailingFast(404))

    fetched = await source.fetch(_task())
    result = await source.extract(_task(), fetched, _request())

    assert fetched is not None
    assert fetched.status_code == 404
    assert fetched.blocked is False
    assert result.blocked is False
    assert [task.url for task in result.discovered_tasks] == [
        "https://dom.mingkh.ru/sitemap.xml"
    ]


@pytest.mark.asyncio
async def test_server_error_stops_source_navigation():
    source = SitemapDiscoveryAdapter(Settings(), fast=_FailingFast(503))

    fetched = await source.fetch(_task())
    result = await source.extract(_task(), fetched, _request())

    assert fetched is not None
    assert fetched.metadata["robots_access_state"] == "unreachable"
    assert result.blocked is True
    assert result.discovered_tasks == []
    assert [error.code for error in result.errors] == ["SOURCE_ROBOTS_UNREACHABLE"]
