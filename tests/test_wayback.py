import json
from pathlib import Path

import httpx
import pytest

from argus.config import Settings
from argus.contracts.models import CollectionRequest
from argus.history.snapshots import SnapshotService
from argus.history.wayback import WaybackCDXProvider
from argus.sources.wayback import WaybackSourceAdapter
from argus.storage.sqlite import SQLiteRepository


def settings(tmp_path: Path, **values) -> Settings:
    payload = {
        "db_path": tmp_path / "db.sqlite",
        "token_file": tmp_path / "token",
        "wayback_cdx_url": "https://web.archive.org/cdx/search/cdx",
        "wayback_capture_base_url": "https://web.archive.org/web",
        "wayback_min_interval_seconds": 0,
        "direct_provider_retry_base_seconds": 0,
        "direct_provider_retry_max_seconds": 0,
    }
    payload.update(values)
    return Settings(**payload)


def cdx_payload():
    return [
        ["timestamp", "original", "mimetype", "statuscode", "digest", "length"],
        [
            "20150102030405",
            "https://example.com/page",
            "text/html",
            "200",
            "ABC123",
            "1234",
        ],
    ]


@pytest.mark.asyncio
async def test_wayback_provider_uses_exact_url_json_cdx_and_builds_capture_url(tmp_path: Path):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            content=json.dumps(cdx_payload()).encode(),
            request=request,
        )

    provider = WaybackCDXProvider(
        settings(tmp_path),
        transport=httpx.MockTransport(handler),
    )
    result = await provider.captures("https://example.com/page", limit=3)

    assert not result.errors
    assert result.blocked is False
    assert len(result.captures) == 1
    capture = result.captures[0]
    assert capture.timestamp == "20150102030405"
    assert capture.status_code == 200
    assert capture.length == 1234
    assert capture.capture_url == (
        "https://web.archive.org/web/20150102030405id_/https://example.com/page"
    )
    assert capture.captured_at is not None
    assert len(requests) == 1
    params = requests[0].url.params
    assert params["url"] == "https://example.com/page"
    assert params["matchType"] == "exact"
    assert params["output"] == "json"
    assert params["filter"] == "statuscode:200"
    assert params["collapse"] == "digest"
    assert params["limit"] == "3"


@pytest.mark.asyncio
async def test_wayback_empty_capture_set_is_normal_empty_result(tmp_path: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        payload = [["timestamp", "original", "mimetype", "statuscode", "digest", "length"]]
        return httpx.Response(200, content=json.dumps(payload).encode(), request=request)

    provider = WaybackCDXProvider(
        settings(tmp_path),
        transport=httpx.MockTransport(handler),
    )
    result = await provider.captures("https://example.com/missing")
    assert result.captures == []
    assert result.errors == []
    assert result.blocked is False


@pytest.mark.asyncio
async def test_wayback_retries_503_then_recovers(tmp_path: Path):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, content=b"busy", request=request)
        return httpx.Response(
            200,
            content=json.dumps(cdx_payload()).encode(),
            request=request,
        )

    provider = WaybackCDXProvider(
        settings(tmp_path, direct_provider_max_retries=1),
        transport=httpx.MockTransport(handler),
    )
    result = await provider.captures("https://example.com/page")
    assert calls == 2
    assert len(result.captures) == 1
    assert result.errors == []


@pytest.mark.asyncio
async def test_wayback_rate_limit_is_blocked(tmp_path: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, content=b"rate limited", request=request)

    provider = WaybackCDXProvider(
        settings(tmp_path, direct_provider_max_retries=0),
        transport=httpx.MockTransport(handler),
    )
    result = await provider.captures("https://example.com/page")
    assert result.blocked is True
    assert result.errors[0].code == "ARCHIVE_PROVIDER_BLOCKED"


@pytest.mark.asyncio
async def test_wayback_source_creates_index_evidence_and_archived_page_task(tmp_path: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=json.dumps(cdx_payload()).encode(),
            request=request,
        )

    repo = SQLiteRepository(tmp_path / "db.sqlite")
    await repo.initialize()
    provider = WaybackCDXProvider(
        settings(tmp_path),
        transport=httpx.MockTransport(handler),
    )
    adapter = WaybackSourceAdapter(provider, SnapshotService(repo))
    request = CollectionRequest(
        consumer="test",
        analysis_id="history-1",
        territory={"city": "Ижевск"},
        intents=["historical_context"],
        constraints={"seed_urls": ["https://example.com/page"]},
    )
    tasks = await adapter.discover(request)
    assert len(tasks) == 1
    tasks[0].metadata["collection_id"] = "collection-1"

    result = await adapter.extract(tasks[0], await adapter.fetch(tasks[0]), request)
    assert len(result.observations) == 1
    assert len(result.evidence) == 1
    assert len(result.discovered_tasks) == 1
    observation = result.observations[0]
    assert observation.source_kind == "archive_capture_index"
    assert observation.published_at is not None
    assert observation.provenance["snapshot_id"]
    assert result.evidence[0].source.url == observation.url
    archived_task = result.discovered_tasks[0]
    assert archived_task.source_id == "generic_web"
    assert archived_task.url == observation.url
    assert archived_task.metadata["discovery_provider"] == "wayback_cdx"
