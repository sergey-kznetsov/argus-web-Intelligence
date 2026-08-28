# Windows PostgreSQL + Playwright runtime

ARGUS server deployment on Windows has two incompatible asyncio requirements that must
be separated deliberately.

- Psycopg 3 async does not support Windows' `ProactorEventLoop` and requires a
  `SelectorEventLoop`.
- Playwright launches its driver as an asyncio subprocess. On Windows subprocess support
  is provided by `ProactorEventLoop`, not `SelectorEventLoop`.

The Geo Analyzer deployment entrypoint therefore uses this process model:

```text
ARGUS API / worker / storage command
        |
        +-- Windows SelectorEventLoop     -> Psycopg / PostgreSQL
        |
        +-- dedicated browser thread
                |
                +-- Windows ProactorEventLoop -> Crawlee / Playwright / Chromium
```

`argus.runtime_entrypoint` must be used by `geo-analyzer-module.json` for PostgreSQL
migrations, API startup and worker startup. On Linux/macOS the compatibility setup is a
no-op and the ordinary event-loop behavior remains unchanged.

## Uvicorn API loop

Uvicorn 0.36+ creates the server loop through `Config.get_loop_factory()`. Its built-in
single-process Windows asyncio factory returns a `ProactorEventLoop`; changing the global
asyncio policy is therefore not sufficient for the API process. ARGUS passes
`argus.platform_asyncio:postgres_server_event_loop_factory` to Uvicorn so the API server
itself is created on `SelectorEventLoop`. Worker and storage commands continue to use the
Windows selector policy before their asyncio runtime is created.

The browser adapter owns exactly one dedicated thread and creates the complete
`BrowserCrawlerRuntime` inside that thread. Playwright objects are never shared across
threads. Calls from the worker are forwarded with `asyncio.run_coroutine_threadsafe()`;
cancellation is propagated to the browser future and shutdown drains the browser before
stopping its Proactor loop.

This is not a CAPTCHA/anti-bot bypass and does not alter ARGUS network security,
`UrlGuard`, browser sandbox settings, Evidence rules or source policies. It only separates
platform event-loop responsibilities.

The CI pipeline contains a Windows Server 2022 / Python 3.11 / PostgreSQL 14 deployment
smoke. It runs the same storage migration/check entrypoints as Geo Analyzer, starts the
real API and worker processes, waits for authenticated API health and worker readiness,
and fails immediately with both process logs if either process exits. Unit regressions
also verify the Selector API loop and the separate Proactor browser loop.
