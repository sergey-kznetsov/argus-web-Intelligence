# Windows runtime: PostgreSQL + Playwright

На Windows server ARGUS одновременно имеет два несовместимых asyncio requirements, поэтому они разделены по execution loops.

- Psycopg 3 async не поддерживает Windows `ProactorEventLoop` и требует `SelectorEventLoop`.
- Playwright запускает driver как asyncio subprocess; на Windows subprocess support даёт `ProactorEventLoop`, а не Selector.

Standalone Windows runtime использует:

```text
ARGUS API / worker / storage command
  |
  +-- Windows SelectorEventLoop -> Psycopg / PostgreSQL
  |
  +-- dedicated browser thread
       |
       +-- Windows ProactorEventLoop -> Crawlee / Playwright / Chromium
```

`argus.runtime_entrypoint` используется standalone deployment для migrations, API и worker startup. На Linux/macOS compatibility setup — no-op.

## Uvicorn API loop

Uvicorn 0.36+ создаёт server loop через `Config.get_loop_factory()`. Built-in Windows asyncio factory в single process возвращает `ProactorEventLoop`, поэтому одной global asyncio policy недостаточно.

ARGUS передаёт `argus.platform_asyncio:postgres_server_event_loop_factory`, чтобы API server создавался на `SelectorEventLoop`. Worker/storage commands устанавливают Windows selector policy до создания asyncio runtime.

Browser adapter владеет одним dedicated thread и создаёт весь `BrowserCrawlerRuntime` внутри него. Playwright objects не передаются между threads. Worker calls пробрасываются через `asyncio.run_coroutine_threadsafe()`; cancellation передаётся browser future, shutdown сначала закрывает browser и затем Proactor loop.

Это platform compatibility layer. Он не меняет UrlGuard, browser sandbox, Evidence rules, source policies и не относится к обходу anti-bot/access controls.

CI содержит Windows Server 2022 / Python 3.11 / PostgreSQL 14 deployment smoke: запускаются те же storage migrate/check entrypoints, реальные API/worker processes, authenticated API health и worker readiness. Unit regressions проверяют Selector API loop и отдельный Proactor browser loop.
