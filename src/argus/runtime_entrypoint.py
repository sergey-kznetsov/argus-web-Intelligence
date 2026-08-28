from __future__ import annotations

import argparse
import asyncio
import sys

from argus.config import Settings, get_settings
from argus.platform_asyncio import (
    configure_windows_postgres_event_loop,
    windows_postgres_selector_required,
)

_POSTGRES_UVICORN_LOOP_FACTORY = "argus.platform_asyncio:postgres_server_event_loop_factory"


def _prepare_windows_runtime(settings: Settings) -> bool:
    """Configure Psycopg and Playwright to use compatible Windows event loops."""

    configured = configure_windows_postgres_event_loop(settings.storage_backend)
    if not configured:
        return False

    # Patch before importing bootstrap/api/worker: those modules import the browser class
    # into their module namespace. The wrapper keeps Playwright on a dedicated Proactor
    # loop while this PostgreSQL-backed process runs on SelectorEventLoop.
    from argus.crawler.browser import runtime as browser_runtime_module
    from argus.crawler.browser.windows_runtime import WindowsProactorBrowserRuntime

    browser_runtime_module.BrowserCrawlerRuntime = WindowsProactorBrowserRuntime
    return True


def _api_loop_factory(settings: Settings, *, platform: str | None = None) -> str:
    if windows_postgres_selector_required(settings.storage_backend, platform=platform):
        return _POSTGRES_UVICORN_LOOP_FACTORY
    return "asyncio"


def _run_storage(storage_args: list[str]) -> None:
    configure_windows_postgres_event_loop("postgresql")
    from argus.storage.cli import main as storage_main

    previous = sys.argv
    try:
        sys.argv = ["python -m argus.storage.cli", *storage_args]
        storage_main()
    finally:
        sys.argv = previous


def _run_api(host: str, port: int) -> None:
    settings = get_settings()
    _prepare_windows_runtime(settings)
    import uvicorn
    from argus.api.app import app

    # Uvicorn 0.36+ creates its own loop from Config.get_loop_factory(). On Windows its
    # built-in single-process asyncio factory is ProactorEventLoop, which is incompatible
    # with Psycopg async even after setting WindowsSelectorEventLoopPolicy. Supply ARGUS'
    # explicit Selector factory to Uvicorn itself instead of relying on process policy.
    uvicorn.run(app, host=host, port=port, loop=_api_loop_factory(settings))


def _run_worker(probe_host: str, probe_port: int) -> None:
    settings = get_settings()
    _prepare_windows_runtime(settings)
    from argus.observability import configure_logging
    from argus.worker import run_worker

    configure_logging(settings.log_level)
    asyncio.run(
        run_worker(
            settings,
            probe_host=probe_host,
            probe_port=probe_port,
        )
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m argus.runtime_entrypoint")
    commands = parser.add_subparsers(dest="command", required=True)

    storage = commands.add_parser("storage")
    storage.add_argument("storage_args", nargs=argparse.REMAINDER)

    api = commands.add_parser("api")
    api.add_argument("--host", default="127.0.0.1")
    api.add_argument("--port", type=int, required=True)

    worker = commands.add_parser("worker")
    worker.add_argument("--probe-host", default="127.0.0.1")
    worker.add_argument("--probe-port", type=int, default=0)
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.command == "storage":
        if not args.storage_args:
            raise SystemExit("storage command is required")
        _run_storage(list(args.storage_args))
    elif args.command == "api":
        _run_api(args.host, args.port)
    elif args.command == "worker":
        _run_worker(args.probe_host, args.probe_port)
    else:
        raise SystemExit(f"unsupported runtime command: {args.command}")


if __name__ == "__main__":
    main()
