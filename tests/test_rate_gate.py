import asyncio

import pytest

from argus.network.rate_gate import AsyncRateGate


@pytest.mark.asyncio
async def test_rate_gate_delays_second_request_without_real_sleep(monkeypatch):
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    gate = AsyncRateGate(1.0)

    await gate.wait()
    await gate.wait()

    assert len(delays) == 1
    assert 0.9 <= delays[0] <= 1.0


@pytest.mark.asyncio
async def test_rate_gate_can_be_disabled(monkeypatch):
    called = False

    async def fake_sleep(delay: float) -> None:
        nonlocal called
        called = True
        del delay

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    gate = AsyncRateGate(0)

    await gate.wait()
    await gate.wait()

    assert called is False
