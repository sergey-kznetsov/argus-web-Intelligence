from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from argus.api.app import create_app
from argus.config import Settings
from argus.contracts.models import CollectionRequest
from argus.crawler.models import FetchResult
from argus.observability import OperationalMetrics
from argus.sources.base import SourceResult, SourceTask
from argus.sources.registry import SourceRegistry


def auth_headers(settings: Settings) -> dict[str, str]:
    token = settings.token_file.read_text().strip()
    return {"Authorization": f"Bearer {token}"}


def test_operational_metrics_reject_high_cardinality_labels():
    metrics = OperationalMetrics()

    with pytest.raises(ValueError, match="high-cardinality"):
        metrics.inc("requests_total", collection_id="collection-1")
    with pytest.raises(ValueError, match="high-cardinality"):
        metrics.inc("requests_total", url="https://example.com/page")
    with pytest.raises(ValueError, match="high-cardinality"):
        metrics.inc("requests_total", consumer="kraken")

    metrics.inc("requests_total", source_id="generic_web", status="ok")
    rows = metrics.snapshot()["counters"]["requests_total"]
    assert rows == [
        {
            "labels": {"source_id": "generic_web", "status": "ok"},
            "value": 1,
        }
    ]


def test_operational_metrics_reject_excess_label_dimensions():
    metrics = OperationalMetrics()

    with pytest.raises(ValueError, match="label count"):
        metrics.inc(
            "requests_total",
            a=1,
            b=2,
            c=3,
            d=4,
            e=5,
            f=6,
            g=7,
        )


def test_operational_metrics_drop_series_over_cardinality_budget():
    metrics = OperationalMetrics()
    metrics.max_series_per_metric = 2

    metrics.inc("source_total", source_id="a")
    metrics.inc("source_total", source_id="b")
    metrics.inc("source_total", source_id="c")

    snapshot = metrics.snapshot()
    assert len(snapshot["counters"]["source_total"]) == 2
    assert snapshot["dropped_series"]["source_total"] == 1


def test_operational_metrics_endpoint_is_authenticated_and_bounded(tmp_path: Path):
    settings = Settings(db_path=tmp_path / "db.sqlite", token_file=tmp_path / "token")

    with TestClient(create_app(settings)) as client:
        assert client.get("/v1/operations/metrics").status_code == 401

        response = client.get(
            "/v1/operations/metrics",
            headers=auth_headers(settings),
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["execution_role"] == "embedded"
        assert payload["storage_backend"] == "sqlite"
        assert payload["queue"] is None
        assert payload["process"]["version"] == "argus-operational-metrics/1"
        policy = payload["process"]["cardinality_policy"]
        assert policy["collection_id_labels"] is False
        assert policy["consumer_labels"] is False
        assert policy["url_labels"] is False
        assert "collection_id" in policy["forbidden_labels"]
        assert payload["exporters"] == {
            "prometheus": False,
            "opentelemetry": False,
            "built_in_json": True,
        }

        capabilities = client.get(
            "/v1/capabilities",
            headers=auth_headers(settings),
        ).json()
        assert capabilities["operations"]["runtime_metrics"] is True


class DummyBrowserAdapter:
    source_id = "dummy"
    intents = {"*"}

    async def discover(self, request: CollectionRequest) -> list[SourceTask]:
        del request
        return []

    async def fetch(self, task: SourceTask) -> FetchResult:
        return FetchResult(
            url=task.url,
            final_url=task.url,
            status_code=200,
            content_type="text/html",
            text="ok",
            runtime="browser",
        )

    async def extract(self, task, fetched, request) -> SourceResult:
        del task, fetched, request
        return SourceResult(observations=[])

    async def normalize(self, result: SourceResult) -> SourceResult:
        return result

    async def health(self) -> dict[str, object]:
        return {"status": "ok"}


@pytest.mark.asyncio
async def test_source_registry_records_runtime_and_escalation_metrics():
    metrics = OperationalMetrics()
    registry = SourceRegistry(metrics=metrics)
    registry.register(DummyBrowserAdapter())
    source = registry.get("dummy")
    task = SourceTask(
        source_id="dummy",
        goal="mentions",
        url="https://example.com",
    )

    fetched = await source.fetch(task)
    result = await source.extract(task, fetched, object())
    await source.normalize(result)

    snapshot = metrics.snapshot()
    fetch_rows = snapshot["counters"]["source_fetch_total"]
    assert fetch_rows[0]["labels"] == {
        "runtime": "browser",
        "source_id": "dummy",
        "status": "ok",
    }
    escalation = snapshot["counters"]["runtime_escalation_total"]
    assert escalation[0]["labels"]["runtime"] == "browser"
    assert snapshot["durations"]["source_fetch_duration_seconds"][0]["count"] == 1
