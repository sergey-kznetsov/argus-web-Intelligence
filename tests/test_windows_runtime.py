from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path

import pytest

from argus.config import Settings
from argus.crawler.browser.windows_runtime import WindowsProactorBrowserRuntime
from argus.platform_asyncio import (
    configure_windows_postgres_event_loop,
    postgres_server_event_loop_factory,
    windows_postgres_selector_required,
)
from argus.security.urls import UrlGuard


class _FakeBrowserRuntime:
    shutdown_calls = 0

    def __init__(self, settings: Settings, url_guard: UrlGuard) -> None:
        del settings, url_guard

    async def fetch(self, url: str, recipe=None):
        del url, recipe
        return {
            "thread_id": threading.get_ident(),
            "loop_type": type(asyncio.get_running_loop()).__name__,
        }

    async def shutdown(self) -> None:
        type(self).shutdown_calls += 1


class _SubprocessBrowserRuntime(_FakeBrowserRuntime):
    async def fetch(self, url: str, recipe=None):
        del url, recipe
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "print('argus-proactor-ok')",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        return {
            "thread_id": threading.get_ident(),
            "loop_type": type(asyncio.get_running_loop()).__name__,
            "returncode": process.returncode,
            "stdout": stdout.decode().strip(),
            "stderr": stderr.decode().strip(),
        }


def test_windows_selector_requirement_is_platform_and_backend_scoped() -> None:
    assert windows_postgres_selector_required("postgresql", platform="win32")
    assert not windows_postgres_selector_required("sqlite", platform="win32")
    assert not windows_postgres_selector_required("postgresql", platform="linux")


def test_selector_policy_configuration_is_noop_off_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    def _set_policy(policy) -> None:
        nonlocal called
        del policy
        called = True

    monkeypatch.setattr(asyncio, "set_event_loop_policy", _set_policy)
    assert not configure_windows_postgres_event_loop("postgresql", platform="linux")
    assert not called


def test_threaded_browser_runtime_dispatches_to_dedicated_loop() -> None:
    main_thread = threading.get_ident()
    _FakeBrowserRuntime.shutdown_calls = 0

    async def _exercise() -> dict[str, object]:
        runtime = WindowsProactorBrowserRuntime(
            Settings(),
            UrlGuard.from_strings([]),
            loop_factory=asyncio.new_event_loop,
            runtime_factory=_FakeBrowserRuntime,
        )
        try:
            return await runtime.fetch("https://example.com")
        finally:
            await runtime.shutdown()

    result = asyncio.run(_exercise())
    assert result["thread_id"] != main_thread
    assert _FakeBrowserRuntime.shutdown_calls >= 1


def test_standalone_windows_deployment_uses_runtime_entrypoint() -> None:
    deploy = Path("deploy/windows/deploy-server.ps1").read_text(encoding="utf-8")
    runner = Path("deploy/windows/run-process.ps1").read_text(encoding="utf-8")

    assert '"argus.runtime_entrypoint", "storage", "migrate"' in deploy
    assert '"argus.runtime_entrypoint", "storage", "check"' in deploy
    assert '"-m", "argus.runtime_entrypoint", "api"' in runner
    assert '"-m", "argus.runtime_entrypoint", "worker"' in runner
    assert '$ErrorActionPreference = "Continue"' in runner
    assert "$ErrorActionPreference = $previousPreference" in runner
    assert "exit $processExitCode" in runner
    assert not Path("geo-analyzer-module.json").exists()


def test_api_loop_factory_is_scoped_to_windows_postgresql() -> None:
    from argus.runtime_entrypoint import _POSTGRES_UVICORN_LOOP_FACTORY, _api_loop_factory

    postgres = Settings(storage_backend="postgresql")
    sqlite = Settings(storage_backend="sqlite")
    assert _api_loop_factory(postgres, platform="win32") == _POSTGRES_UVICORN_LOOP_FACTORY
    assert _api_loop_factory(sqlite, platform="win32") == "asyncio"
    assert _api_loop_factory(postgres, platform="linux") == "asyncio"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows event loops only")
def test_windows_psycopg_async_accepts_selector_loop() -> None:
    import psycopg

    assert configure_windows_postgres_event_loop("postgresql")

    async def _connect() -> None:
        with pytest.raises(psycopg.OperationalError):
            await psycopg.AsyncConnection.connect(
                "host=127.0.0.1 port=1 dbname=argus user=argus connect_timeout=1"
            )

    asyncio.run(_connect())


@pytest.mark.skipif(sys.platform != "win32", reason="Windows event loops only")
def test_windows_uvicorn_postgres_factory_is_selector() -> None:
    from uvicorn import Config

    loop = postgres_server_event_loop_factory()
    try:
        assert "Selector" in type(loop).__name__
    finally:
        loop.close()

    config = Config(
        "argus.api.app:app",
        loop="argus.platform_asyncio:postgres_server_event_loop_factory",
    )
    factory = config.get_loop_factory()
    assert factory is postgres_server_event_loop_factory
    uvicorn_loop = factory()
    try:
        assert "Selector" in type(uvicorn_loop).__name__
    finally:
        uvicorn_loop.close()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows event loops only")
def test_windows_postgres_and_browser_use_split_event_loops() -> None:
    assert configure_windows_postgres_event_loop("postgresql")
    selector_loop = asyncio.new_event_loop()
    assert "Selector" in type(selector_loop).__name__

    async def _exercise() -> dict[str, object]:
        runtime = WindowsProactorBrowserRuntime(
            Settings(),
            UrlGuard.from_strings([]),
            runtime_factory=_SubprocessBrowserRuntime,
        )
        try:
            return await runtime.fetch("https://example.com")
        finally:
            await runtime.shutdown()

    try:
        asyncio.set_event_loop(selector_loop)
        result = selector_loop.run_until_complete(_exercise())
    finally:
        asyncio.set_event_loop(None)
        selector_loop.close()

    assert "Proactor" in str(result["loop_type"])
    assert result["returncode"] == 0
    assert result["stdout"] == "argus-proactor-ok"
