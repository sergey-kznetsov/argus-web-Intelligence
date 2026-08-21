from __future__ import annotations

import asyncio


class AsyncRateGate:
    """Space direct-provider request starts without blocking unrelated ARGUS traffic."""

    def __init__(self, min_interval_seconds: float) -> None:
        self.min_interval_seconds = max(0.0, float(min_interval_seconds))
        self._lock = asyncio.Lock()
        self._next_allowed = 0.0

    async def wait(self) -> None:
        if self.min_interval_seconds <= 0:
            return
        loop = asyncio.get_running_loop()
        async with self._lock:
            delay = self._next_allowed - loop.time()
            if delay > 0:
                await asyncio.sleep(delay)
            self._next_allowed = loop.time() + self.min_interval_seconds
