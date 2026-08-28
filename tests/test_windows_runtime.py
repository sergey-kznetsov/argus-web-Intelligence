from __future__ import annotations

import asyncio
import json
import sys
import threading
from pathlib import Path

import pytest

from argus.config import Settings
from argus.crawler.browser.windows_runtime import WindowsProactorBrowserRuntime
from argus.platform_asyncio import (
    configure_windows_postgres_event_loop,
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


def test_deployment_manifest_uses_runtime_entrypoint() -> None:
    manifest = json.loads(Path("geo-analyzer-module.json").read_text(encoding="utf-8"))
    migration_commands = manifest["database"]["migrations"]
    process_commands = [process["command"] for process in manifest["processes"]]
    assert all("argus.runtime_entrypoint" in command for command in migration_commands)
    assert all("argus.runtime_entrypoint" in command for command in process_commands)


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
    assert result["stderr"] == ""


def test_runtime_entrypoint_storage_configures_policy_before_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from argus import runtime_entrypoint
    from argus.storage import cli as storage_cli

    calls: list[object] = []

    def _configure(storage_backend: str) -> bool:
        calls.append(("configure", storage_backend))
        return True

    def _storage_main() -> None:
        calls.append(("main", tuple(sys.argv)))

    monkeypatch.setattr(runtime_entrypoint, "configure_windows_postgres_event_loop", _configure)
    monkeypatch.setattr(storage_cli, "main", _storage_main)
    original_argv = list(sys.argv)

    runtime_entrypoint._run_storage(["migrate"])

    assert calls[0] == ("configure", "postgresql")
    assert calls[1] == ("main", ("python -m argus.storage.cli", "migrate"))
    assert sys.argv == original_argv


def test_runtime_entrypoint_parser_accepts_deployment_commands() -> None:
    from argus.runtime_entrypoint import _parser

    parser = _parser()
    storage = parser.parse_args(["storage", "migrate"])
    api = parser.parse_args(["api", "--port", "18100"])
    worker = parser.parse_args(["worker", "--probe-port", "18101"])

    assert storage.command == "storage"
    assert storage.storage_args == ["migrate"]
    assert api.command == "api"
    assert api.host == "127.0.0.1"
    assert api.port == 18100
    assert worker.command == "worker"
    assert worker.probe_host == "127.0.0.1"
    assert worker.probe_port == 18101
