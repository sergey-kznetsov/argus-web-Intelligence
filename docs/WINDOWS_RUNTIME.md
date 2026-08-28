# Windows PostgreSQL + Playwright runtime

ARGUS server deployment on Windows has two incompatible asyncio requirements that must
be separated deliberately.

- Psycopg 3 async does not support Windows' default `ProactorEventLoop` and requires a
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

The browser adapter owns exactly one dedicated thread and creates the complete
`BrowserCrawlerRuntime` inside that thread. Playwright objects are never shared across
threads. Calls from the worker are forwarded with `asyncio.run_coroutine_threadsafe()`;
cancellation is propagated to the browser future and shutdown drains the browser before
stopping its Proactor loop.

This is not a CAPTCHA/anti-bot bypass and does not alter ARGUS network security,
`UrlGuard`, browser sandbox settings, Evidence rules or source policies. It only separates
platform event-loop responsibilities.

The CI pipeline contains a Windows Python 3.11 compatibility job because Geo Analyzer
TEST currently deploys ARGUS on Windows Server with Python 3.11. The regression verifies
that PostgreSQL mode creates a Selector loop while the browser side runs on a separate
Proactor loop.
