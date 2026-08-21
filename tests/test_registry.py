import pytest

from argus.sources.base import SourceResult, SourceTask
from argus.sources.registry import SourceRegistry


class Source:
    source_id = "x"
    intents = {"reviews"}

    async def discover(self, request):
        del request
        return []

    async def fetch(self, task):
        return task

    async def extract(self, task, fetched, request):
        del task, fetched, request
        return SourceResult(observations=[])

    async def normalize(self, result):
        return result

    async def health(self):
        return {"source_id": self.source_id, "status": "configured"}


class BlockedSource(Source):
    source_id = "blocked"

    async def extract(self, task, fetched, request):
        del task, fetched, request
        return SourceResult(observations=[], blocked=True)


def test_registry_filters_intents():
    registry = SourceRegistry()
    registry.register(Source())
    assert len(registry.for_intents(["reviews"])) == 1
    assert len(registry.for_intents(["local_news"])) == 0


@pytest.mark.asyncio
async def test_registry_health_distinguishes_ready_from_last_success():
    registry = SourceRegistry()
    registry.register(Source())
    health = await registry.health("x")
    assert health["status"] == "ready"
    assert health["adapter_status"] == "configured"
    assert health["operational"]["last_attempt_at"] is None

    source = registry.get("x")
    task = SourceTask(source_id="x", goal="reviews", url="https://example.com")
    fetched = await source.fetch(task)
    result = await source.extract(task, fetched, object())
    await source.normalize(result)

    health = await registry.health("x")
    assert health["status"] == "ok"
    assert health["operational"]["last_attempt_at"] is not None
    assert health["operational"]["last_success_at"] is not None


@pytest.mark.asyncio
async def test_registry_health_reports_blocked_result():
    registry = SourceRegistry()
    registry.register(BlockedSource())
    source = registry.get("blocked")
    task = SourceTask(source_id="blocked", goal="reviews", url="https://example.com")
    fetched = await source.fetch(task)
    result = await source.extract(task, fetched, object())
    await source.normalize(result)

    health = await registry.health("blocked")
    assert health["status"] == "blocked"
    assert health["operational"]["last_error_code"] == "SOURCE_BLOCKED"
