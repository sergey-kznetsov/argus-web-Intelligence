# Standalone Web UI ARGUS

ARGUS остаётся headless web-intelligence backend. Необязательный Web UI — отдельный operator gateway, а не второй crawler/orchestrator.

```text
Browser
  -> ARGUS Web UI gateway (argus-web)
  -> local ARGUS API (/v1/collections)
  -> Research Planner / SourceRegistry
  -> FAST -> BROWSER
  -> Evidence / Snapshots / Storage

Geo Analyzer modules
  -> тот же local ARGUS API
```

AGENT в текущем service graph не подключён, поэтому старое описание `FAST -> BROWSER -> AGENT` для UI больше не соответствует runtime.

Gateway не может обходить CollectionRequest, planners, source registry, budgets, security, provenance или persistence rules.

## Запуск

Сначала запускается normal ARGUS API. Для standalone SQLite:

```bash
ARGUS_EXECUTION_ROLE=embedded \
ARGUS_STORAGE_BACKEND=sqlite \
ARGUS_HOST=127.0.0.1 \
ARGUS_PORT=8787 \
argus serve
```

Затем gateway:

```bash
ARGUS_WEB_API_URL=http://127.0.0.1:8787 \
ARGUS_WEB_API_TOKEN_FILE=.argus/token \
ARGUS_WEB_HOST=127.0.0.1 \
ARGUS_WEB_PORT=8790 \
argus-web
```

Открыть `http://127.0.0.1:8790/`.

В server deployment Web UI указывает на standalone localhost ARGUS API и не получает отдельную DB.

## Authentication

Browser не получает internal Bearer token. `argus-web` читает его из `ARGUS_WEB_API_TOKEN_FILE` и добавляет только к server-side requests.

Сам Web UI защищён HTTP Basic. Username по умолчанию `argus`. При первом запуске random password создаётся в `.argus/web-password` либо path из `ARGUS_WEB_PASSWORD_FILE`. Password не логируется.

Нельзя публиковать UI напрямую по plain HTTP в public network. Для remote access process должен остаться loopback, а внешний доступ — через HTTPS reverse proxy с отдельной network/access policy.

## Security boundary

Gateway не является generic reverse proxy. Он exposes только фиксированный набор нужных UI operations:

- health/capabilities;
- source list;
- submit collection;
- collection status;
- cancel;
- result summary;
- paged observations/evidence.

`ARGUS_WEB_API_URL` разрешает только loopback hosts (`127.0.0.1`, `localhost`, `::1`), чтобы UI не стал SSRF primitive.

UI отдаёт restrictive CSP, запрещает framing, использует `no-store` и выводит returned payload как text, не инжектя fetched HTML.

## Product boundary

Web UI — ещё один consumer ARGUS API. Source-specific scraping, research heuristics, business logic и storage не должны переноситься в gateway. Общая возможность реализуется в core API/orchestrator.
