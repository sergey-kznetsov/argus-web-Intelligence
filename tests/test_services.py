import asyncio

import pytest

from argus.services import ServiceContainer


class _FakeOrchestrator:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def start(self) -> None:
        self.events.append("orchestrator:start")

    async def shutdown(self) -> None:
        self.events.append("orchestrator:stop")


class _FakeRuntime:
    def __init__(self, name: str, events: list[str]) -> None:
        self.name = name
        self.events = events

    async def shutdown(self) -> None:
        await asyncio.sleep(0)
        self.events.append(f"{self.name}:stop")


@pytest.mark.asyncio
async def test_service_container_stops_jobs_before_crawlers():
    events: list[str] = []
    services = ServiceContainer(
        repository=object(),
        registry=object(),
        orchestrator=_FakeOrchestrator(events),
        fast=_FakeRuntime("fast", events),
        browser=_FakeRuntime("browser", events),
    )

    await services.start()
    await services.shutdown()

    assert events[0] == "orchestrator:start"
    assert events[1] == "orchestrator:stop"
    assert set(events[2:]) == {"fast:stop", "browser:stop"}
