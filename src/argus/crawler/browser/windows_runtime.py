from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from contextlib import suppress
from typing import Any

from argus.config import Settings
from argus.crawler.browser.runtime import BrowserCrawlerRuntime as DirectBrowserCrawlerRuntime
from argus.platform_asyncio import create_windows_proactor_event_loop
from argus.recipes.models import SiteRecipe
from argus.security.urls import UrlGuard


class WindowsProactorBrowserRuntime(DirectBrowserCrawlerRuntime):
    """Run Crawlee/Playwright on a dedicated Windows Proactor event-loop thread.

    PostgreSQL-backed ARGUS processes use SelectorEventLoop on Windows because Psycopg
    async is incompatible with ProactorEventLoop. Playwright needs subprocess support,
    which Windows provides through ProactorEventLoop. This adapter keeps the complete
    browser runtime on one dedicated Proactor thread and exposes the normal async browser
    interface to the Selector-based API/worker process.
    """

    def __init__(
        self,
        settings: Settings,
        url_guard: UrlGuard,
        *,
        loop_factory: Callable[[], asyncio.AbstractEventLoop] | None = None,
        runtime_factory: Callable[[Settings, UrlGuard], Any] | None = None,
    ) -> None:
        # Do not initialize DirectBrowserCrawlerRuntime on the caller thread: its asyncio
        # locks and all later Crawlee/Playwright state must belong to the Proactor loop.
        self.settings = settings
        self.url_guard = url_guard
        self._loop_factory = loop_factory or create_windows_proactor_event_loop
        self._runtime_factory = runtime_factory or DirectBrowserCrawlerRuntime
        self._state_lock = threading.Lock()
        self._shutdown_lock = asyncio.Lock()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._runtime: Any | None = None
        self._startup_error: Exception | None = None
        self._runtime_shutdown = False
        self._closed = False

    @staticmethod
    def security_options() -> dict[str, object]:
        return DirectBrowserCrawlerRuntime.security_options()

    async def fetch(self, url: str, recipe: SiteRecipe | None = None):
        if self._closed:
            raise RuntimeError("Windows BROWSER runtime is closed")
        await asyncio.to_thread(self._ensure_thread_started)
        loop, runtime = self._running_handles()
        future = asyncio.run_coroutine_threadsafe(runtime.fetch(url, recipe), loop)
        try:
            return await asyncio.wrap_future(future)
        except asyncio.CancelledError:
            future.cancel()
            raise

    async def shutdown(self) -> None:
        async with self._shutdown_lock:
            if self._closed:
                return
            self._closed = True

            with self._state_lock:
                thread = self._thread
            if thread is None:
                return

            if not self._ready.is_set():
                ready = await asyncio.to_thread(self._ready.wait, 10.0)
                if not ready:
                    raise RuntimeError("Windows BROWSER Proactor thread startup timed out")

            with self._state_lock:
                loop = self._loop
                runtime = self._runtime
                startup_error = self._startup_error

            shutdown_error: Exception | None = None
            shutdown_cancelled = False
            if (
                startup_error is None
                and loop is not None
                and runtime is not None
                and loop.is_running()
            ):
                future = asyncio.run_coroutine_threadsafe(runtime.shutdown(), loop)
                try:
                    await asyncio.wrap_future(future)
                    with self._state_lock:
                        self._runtime_shutdown = True
                except asyncio.CancelledError:
                    future.cancel()
                    shutdown_cancelled = True
                except Exception as exc:
                    shutdown_error = exc
                finally:
                    loop.call_soon_threadsafe(loop.stop)
            elif loop is not None and loop.is_running():
                loop.call_soon_threadsafe(loop.stop)

            await asyncio.to_thread(thread.join, 15.0)
            if thread.is_alive():
                raise RuntimeError("Windows BROWSER Proactor thread did not stop")

            with self._state_lock:
                self._thread = None
                self._loop = None
                self._runtime = None
                self._startup_error = None
                self._runtime_shutdown = False
                self._ready.clear()

            if shutdown_cancelled:
                raise asyncio.CancelledError
            if shutdown_error is not None:
                raise shutdown_error

    def _ensure_thread_started(self) -> None:
        with self._state_lock:
            if self._closed:
                raise RuntimeError("Windows BROWSER runtime is closed")
            thread = self._thread
            if thread is None or not thread.is_alive():
                self._ready.clear()
                self._startup_error = None
                self._runtime_shutdown = False
                thread = threading.Thread(
                    target=self._thread_main,
                    name="argus-playwright-proactor",
                    daemon=True,
                )
                self._thread = thread
                thread.start()

        if not self._ready.wait(10.0):
            raise RuntimeError("Windows BROWSER Proactor thread startup timed out")
        with self._state_lock:
            error = self._startup_error
        if error is not None:
            raise RuntimeError("Windows BROWSER Proactor thread failed to start") from error

    def _running_handles(self) -> tuple[asyncio.AbstractEventLoop, Any]:
        with self._state_lock:
            loop = self._loop
            runtime = self._runtime
            error = self._startup_error
        if error is not None:
            raise RuntimeError("Windows BROWSER Proactor runtime is unavailable") from error
        if loop is None or runtime is None or not loop.is_running():
            raise RuntimeError("Windows BROWSER Proactor runtime is not running")
        return loop, runtime

    def _thread_main(self) -> None:
        loop: asyncio.AbstractEventLoop | None = None
        runtime: Any | None = None
        try:
            loop = self._loop_factory()
            asyncio.set_event_loop(loop)
            runtime = self._runtime_factory(self.settings, self.url_guard)
            with self._state_lock:
                self._loop = loop
                self._runtime = runtime
            self._ready.set()
            loop.run_forever()
        except Exception as exc:
            with self._state_lock:
                self._startup_error = exc
            self._ready.set()
        finally:
            if loop is not None:
                with self._state_lock:
                    runtime_shutdown = self._runtime_shutdown
                if runtime is not None and not runtime_shutdown and not loop.is_closed():
                    with suppress(Exception):
                        loop.run_until_complete(runtime.shutdown())
                if not loop.is_closed():
                    pending = asyncio.all_tasks(loop)
                    for task in pending:
                        task.cancel()
                    if pending:
                        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                    loop.run_until_complete(loop.shutdown_asyncgens())
                    asyncio.set_event_loop(None)
                    loop.close()
