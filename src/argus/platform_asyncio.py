from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable


def windows_postgres_selector_required(
    storage_backend: str,
    *,
    platform: str | None = None,
) -> bool:
    """Return whether this process needs the Windows selector loop for Psycopg."""

    return (platform or sys.platform) == "win32" and storage_backend == "postgresql"


def configure_windows_postgres_event_loop(
    storage_backend: str,
    *,
    platform: str | None = None,
    policy_factory: Callable[[], asyncio.AbstractEventLoopPolicy] | None = None,
) -> bool:
    """Select the Psycopg-compatible event-loop policy before asyncio starts.

    Psycopg async connections cannot run on Windows' default ProactorEventLoop. ARGUS
    therefore runs PostgreSQL-backed API/worker/storage processes on SelectorEventLoop.
    Playwright remains on a dedicated ProactorEventLoop thread; see windows_runtime.py.
    """

    if not windows_postgres_selector_required(storage_backend, platform=platform):
        return False

    factory = policy_factory or getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if factory is None:
        raise RuntimeError(
            "Windows PostgreSQL runtime requires asyncio.WindowsSelectorEventLoopPolicy"
        )
    asyncio.set_event_loop_policy(factory())
    return True


def create_windows_proactor_event_loop() -> asyncio.AbstractEventLoop:
    """Create the Windows loop required by Playwright subprocess transport."""

    factory = getattr(asyncio, "ProactorEventLoop", None)
    if factory is None:
        raise RuntimeError("Windows Playwright runtime requires asyncio.ProactorEventLoop")
    return factory()
