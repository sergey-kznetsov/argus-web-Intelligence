# ARGUS Web Intelligence

ARGUS `0.3.0` — серверный backend web-intelligence для аналитических модулей экосистемы Geo Analyzer. Сервис собирает факты из публичного интернета по принципу evidence-first: найденный URL, поисковый сниппет или навигационная подсказка сами по себе не считаются фактом. Факт появляется только после получения источника и формирования `Observation` + `Evidence` + `Provenance`.

Основное разделение ответственности:

```text
Geo Analyzer = orchestration + presentation
ARGUS        = find + obtain + prove + store + continue researching
Module       = normalize + interpret + calculate + conclude
```

ARGUS работает как отдельный серверный инфраструктурный сервис. Он не устанавливается через Module Manager Geo Analyzer и не должен появляться отдельной галочкой в пользовательском анализе.

## Текущее состояние реализации

Документ сверён с `main` на 10 сентября 2026 года. Фактическая версия пакета — `0.3.0`, версия Collection Protocol — `1.0.0`, идентификатор сервиса в runtime manifest — `argus.web.intelligence`.

Сейчас в рабочем runtime реализованы:

- отдельные процессы API и worker для серверного режима;
- PostgreSQL-очередь с heartbeat worker'ов, lease на коллекции и SQL lease fencing;
- атомарный claim через `FOR UPDATE ... SKIP LOCKED`;
- FIFO-восстановление незавершённых коллекций;
- replay-safe восстановление после остановки процесса или временного сбоя PostgreSQL;
- идемпотентная постановка коллекций с окном повторного запроса 24 часа по умолчанию;
- глобальный и per-consumer backpressure очереди;
- PostgreSQL storage в отдельной БД `argus`, под ролью `argus`, в schema `argus`;
- SQLite для `embedded`/локальной разработки;
- версионируемые и checksummed миграции PostgreSQL;
- отдельные FAST и BROWSER runtime на Crawlee/Playwright;
- универсальное получение HTML, RSS/Atom, JSON Feed, Sitemap, структурированных данных, PDF, DOCX/XLSX, GeoJSON/KML/KMZ и machine-readable HTML-разметки;
- `dom.mingkh.ru` как отдельный residential adapter;
- PastVu и Wayback-контур для исторических задач;
- OpenStreetMap/Overpass-контур;
- snapshots, diffs, `Observation`, `Evidence`, provenance и техническая оценка качества доказательств;
- SiteRecipe storage/replay infrastructure;
- bounded recursive research и checkpoint recovery;
- bounded выдача больших результатов через summary + keyset pagination;
- source health и operational metrics;
- Bearer auth, SSRF/redirect guards, лимиты запросов/ресурсов и secret-safe logging;
- отдельный операторский Web UI, который работает поверх того же ARGUS API.

### LLM/AGENT-контур

Локальный Ollama включён по умолчанию как необязательный управляющий слой. Он участвует в первичном плане, follow-up, supervision, гипотезах сущностей, семантической разметке точных цитат и AGENT-навигации. При недоступности или timeout модель не останавливает сбор: каждый компонент переходит к детерминированному fallback. Флаг `ARGUS_LLM_REQUIRED=true` предназначен только для установки, где оператор сознательно требует fail-closed при старте worker/embedded.

Рабочая цепочка получения страниц:

```text
research plan
  -> discovery
  -> source adapter
  -> FAST
  -> BROWSER при необходимости
  -> AGENT только когда FAST/BROWSER не смогли получить нужное публичное представление
       -> Recipe
       -> Stagehand
       -> Browser Use
  -> детерминированный Playwright replay SiteRecipe
  -> extraction / normalization
  -> Observation + Evidence + Provenance
  -> storage
```

Все LLM-компоненты одного worker используют общий semaphore с `max_concurrency=1`. Модель предлагает только план или навигационные действия. Её текст не становится фактом; семантическая цитата принимается только после буквальной проверки в полученном источнике. CAPTCHA, login, paywall, access-control и изменяющие состояние действия не обходятся.

Stagehand и Browser Use имеют несовместимые обязательные версии `websockets`, поэтому серверная установка создаёт два проверяемых `pip check` окружения: основной venv со Stagehand и изолированный venv Browser Use. Общий worker всё равно применяет единый последовательный порядок и удерживает глобальный LLM-слот во время изолированного вызова. Подробности: [`docs/LLM_AGENT_RUNTIME.md`](docs/LLM_AGENT_RUNTIME.md).

## Consumer profiles, Tool Packs и Research Profiles

ARGUS остаётся единым backend, но исследовательский контракт зависит от зарегистрированного consumer profile. Выбор делается через декларативные `ConsumerProfileRegistry` и `ToolPackRegistry`, а не через бизнес-ветки вида `if consumer == "kraken"`.

`ResearchProfileRegistry` составляет выполнение из повторно используемых capabilities. Профиль декларативно определяет source families, map providers, необходимость инвентаризации улиц, строгий порядок обязательных линий и bounded optional budget. Orchestrator читает эти данные и не содержит веток по имени consumer или module.

Цепочка разрешения контракта:

```text
Consumer capability
  -> Tool Pack
  -> Research Profile
  -> reusable capabilities
  -> source families / public maps / adapters / completion policy
```

Сейчас зарегистрированы:

- `kraken.development.uds`, profile version `1`, capability `urban_signals`;
- `test`, profile version `1`, capability `generic_research` — только для CI/manual smoke.

Новый модуль, для которого не нужен отдельный серверный consumer policy, может выбрать уже зарегистрированный профиль прямо в `CollectionRequest`:

```json
{
  "consumer": "module.x",
  "research_profile": "test_public_context",
  "research_profile_version": 1
}
```

ARGUS сам создаёт ограниченный profile Tool Pack из capability registry. Клиент не может передать произвольный Tool Pack или adapter allowlist. Искусственный профиль `test_public_context` состоит из существующих `official_government` и `local_media` и contract-тестом доказывает подключение Module X без изменений `ConsumerProfileRegistry`, `ToolPackRegistry` и orchestrator.

Для Kraken допустимые `requested_facts`:

```text
complaint
public_appeal
post
comment
resident_message
local_news_mention
incident_mention
```

Отзывы заведений (`review`) не входят в текущий factual contract Kraken. Публичные карты могут использоваться как территориальный и навигационный контекст, но их information-only observations не передаются Kraken как обычные предметные сообщения; соответствующий Evidence сохраняется отдельно.

Для `urban_signals` действует декларативный обязательный исследовательский контур: ARGUS выполняет обязательные source-contour проходы, затем обязательные публичные карты, и только после этого переходит к ограниченному optional research. На текущей версии `mandatory-coverage/5` эти обязательные линии запускаются даже если seed URL уже формально покрывает intents или коллекция восстановлена из checkpoint.

Добавление нового сценария на уже существующих источниках требует только собрать новый Research Profile из capabilities. Отдельный ConsumerProfile/Tool Pack нужен лишь когда продукту необходима собственная политика допустимых фактов или выдачи. Менять общий orchestrator не требуется. Новый adapter нужен только при появлении нового класса источника.

Для публичных карт обязательная логика проходит все улицы, попавшие в радиус территории, по каждому включённому map-provider, а не ограничивается только исходным адресом. Состояние обязательных линий публикуется как `research_lane_coverage` в result API.

## Discovery

В текущем bootstrap доступны:

- SearXNG, если задан `ARGUS_SEARXNG_URL`;
- бесплатные fallback-провайдеры при `ARGUS_BROWSER_SERP_ENABLED=true`: `duckduckgo_fast`, `mojeek_fast`, `bing_rss`;
- Sitemap navigation;
- source-specific routing, включая `dom.mingkh.ru` -> `mingkh_residential`.

Текущее значение `ARGUS_DISCOVERY_MAX_QUERIES` по умолчанию — `12`.

Discovery остаётся навигацией. Поисковый результат должен привести к реально полученному источнику, прежде чем данные попадут в factual layer.

## OpenStreetMap / Overpass

В `embedded` режиме Overpass остаётся opt-in, если endpoint не задан явно.

В серверных ролях `api`/`worker` ARGUS автоматически включает бесплатный bounded Overpass contour, если `ARGUS_OVERPASS_URL` не задан:

```text
primary:  https://overpass-api.de/api/interpreter
fallback: https://overpass.private.coffee/api/interpreter
```

Для автоматически включённого режима timeout ограничен 15 секундами, допускается один failover/retry. Явная операторская конфигурация не перезаписывается.

Nominatim остаётся opt-in и включается только при заданном `ARGUS_NOMINATIM_URL`.

## Серверное развёртывание

Канонический Windows layout:

```text
C:\argus\releases\<commit>\
C:\ProgramData\ARGUS\argus.env
C:\ProgramData\ARGUS\secrets\argus.token
C:\ProgramData\ARGUS\secrets\database-dsn.txt
C:\ProgramData\ARGUS\logs\
C:\ProgramData\ARGUS\deployment.json
```

Scheduled Tasks:

```text
ARGUS-API
ARGUS-Worker
```

Внутренние endpoints:

```text
API          http://127.0.0.1:8787
worker probe http://127.0.0.1:8788/readyz
```

Geo Analyzer и его модули получают только общий service contract:

```text
ARGUS_SERVICE_BASE_URL=http://127.0.0.1:8787
ARGUS_SERVICE_TOKEN_FILE=C:\ProgramData\ARGUS\secrets\argus.token
```

`deploy/windows/deploy-server.ps1` принимает только конкретный 40-символьный commit SHA. В `-Apply` режиме он требует заранее подготовленный ARGUS-owned DSN-файл. Скрипт проверяет, что DSN указывает именно на PostgreSQL database `argus` и user `argus`. Он не читает `saas.env` Geo Analyzer и не копирует DSN из TEST/PROD автоматически.

Подробнее: [`docs/SERVER_DEPLOYMENT_WINDOWS.md`](docs/SERVER_DEPLOYMENT_WINDOWS.md) и [`docs/POSTGRES_OPERATIONS.md`](docs/POSTGRES_OPERATIONS.md).

## API

Health:

```text
GET  /v1/health
HEAD /v1/health
```

Остальные API endpoints требуют Bearer token:

```text
GET  /v1/manifest
GET  /v1/capabilities

POST /v1/collections
GET  /v1/collections/{collection_id}
POST /v1/collections/{collection_id}/cancel

GET  /v1/collections/{collection_id}/result
GET  /v1/collections/{collection_id}/result/summary
GET  /v1/collections/{collection_id}/result/observations
GET  /v1/collections/{collection_id}/result/evidence

GET  /v1/operations/queue
GET  /v1/operations/collections
GET  /v1/operations/metrics

GET  /v1/sources
GET  /v1/sources/{source_id}/health
```

Отдельного `retry` endpoint сейчас нет. Повторная отправка одной и той же CollectionRequest обрабатывается через idempotency/recovery semantics, а не через `/retry`.

### Readiness

Для server API `GET /v1/health` проверяет не только PostgreSQL, но и наличие свежего heartbeat хотя бы одного worker. Если worker отсутствует, статус API становится `degraded`.

## Очередь и восстановление

```text
consumer module
  -> POST /v1/collections
  -> PostgreSQL: queued collection
  -> worker claim + lease
  -> CollectionOrchestrator
  -> atomic task commit
  -> Observation / Evidence / Snapshot / checkpoint
```

Незавершённый `SourceTask` может быть выполнен повторно после сбоя, но публикация результата задачи происходит атомарно. Snapshot, Observation, Evidence и обновлённый checkpoint фиксируются одной транзакцией. Lease fencing не позволяет старому worker записать данные после передачи lease другому worker.

Подробнее: [`docs/RECOVERY.md`](docs/RECOVERY.md).

## Идемпотентность и backpressure

По умолчанию:

```text
ARGUS_IDEMPOTENCY_WINDOW_SECONDS=86400
ARGUS_QUEUE_MAX_ACTIVE_COLLECTIONS=500
ARGUS_QUEUE_MAX_ACTIVE_PER_CONSUMER=100
ARGUS_QUEUE_RETRY_AFTER_SECONDS=15
```

В пределах idempotency window одинаковый key + одинаковый запрос возвращает существующую collection. Тот же key для другого запроса возвращает `409`. При переполнении consumer quota новый запрос получает `429`, глобальной очереди — `503`; оба ответа содержат `Retry-After`.

## Выдача больших результатов

По умолчанию полный `/result` разрешён, пока одновременно выполняются:

```text
ARGUS_API_FULL_RESULT_MAX_ITEMS=100
ARGUS_API_FULL_RESULT_MAX_BYTES=4194304
```

При превышении лимита `/result` возвращает `409 RESULT_REQUIRES_PAGINATION`. Данные не обрезаются молча. Consumer использует `/result/summary`, `/result/observations` и `/result/evidence` с opaque keyset cursors.

Подробнее: [`docs/RESULT_DELIVERY.md`](docs/RESULT_DELIVERY.md).

## Локальная установка

Требуется Python `3.11+`.

```bash
python -m venv .venv
. .venv/bin/activate              # Windows: .venv\Scripts\activate
pip install -e '.[dev]'
playwright install chromium
argus init-token
argus serve
```

Для рабочего Stagehand-профиля установите `pip install -e '.[stagehand,dev]'`. Browser Use устанавливается отдельно через `pip install -e '.[agent-browser-use]'`; смешивать оба extras в одном venv нельзя из-за несовместимых upstream pins. `deploy/windows/deploy-server.ps1` создаёт оба окружения автоматически.

Локальный режим по умолчанию:

```text
ARGUS_EXECUTION_ROLE=embedded
ARGUS_STORAGE_BACKEND=sqlite
ARGUS_HOST=127.0.0.1
ARGUS_PORT=8787
```

Для воспроизводимой проверки без Geo Analyzer используется `argus probe`. Подробнее: [`docs/STANDALONE_PROBE.md`](docs/STANDALONE_PROBE.md).

## Web UI

`argus-web` — отдельный операторский gateway поверх того же ARGUS API. Он не содержит второй crawler, свою БД или отдельную исследовательскую логику. Внутренний Bearer token браузеру не передаётся; gateway добавляет его только к server-side запросам в ARGUS API.

Подробнее: [`docs/WEB_UI.md`](docs/WEB_UI.md).

## Безопасность

Базовые ограничения:

- loopback-only server API;
- Bearer token и PostgreSQL DSN в secret files;
- HTTP(S)-only arbitrary targets;
- блокировка private/link-local/reserved/cloud-metadata адресов, кроме явного allowlist;
- проверка каждого redirect hop;
- bounded request/response/parser/browser limits;
- ограниченные retries и recursion;
- безопасный XML/JSON/OOXML parsing;
- secret-safe logging;
- запрет обхода CAPTCHA, login, paywall и access-control механизмов.

Application-level SSRF protection — defense in depth. Сетевой egress policy остаётся обязанностью server deployment.

Подробнее: [`docs/SECURITY.md`](docs/SECURITY.md).

## Документация

Основные документы:

- [`docs/ARGUS_CHARTER.md`](docs/ARGUS_CHARTER.md) — назначение и продуктовые границы;
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — фактическая архитектура сервиса;
- [`docs/CONSUMER_PROFILES.md`](docs/CONSUMER_PROFILES.md) — consumer profiles и Tool Packs;
- [`docs/RECOVERY.md`](docs/RECOVERY.md) — recovery/replay/lease fencing;
- [`docs/SERVER_DEPLOYMENT_WINDOWS.md`](docs/SERVER_DEPLOYMENT_WINDOWS.md) — серверная установка;
- [`docs/POSTGRES_OPERATIONS.md`](docs/POSTGRES_OPERATIONS.md) — PostgreSQL migrations/backup/restore/retention;
- [`docs/RESULT_DELIVERY.md`](docs/RESULT_DELIVERY.md) — bounded result delivery;
- [`docs/PUBLIC_MAP_SOURCES.md`](docs/PUBLIC_MAP_SOURCES.md) — текущая роль публичных карт;
- [`docs/RESIDENTIAL_SOURCES.md`](docs/RESIDENTIAL_SOURCES.md) — residential facts и `dom.mingkh.ru`;
- [`docs/SECURITY.md`](docs/SECURITY.md) — security boundary.
- [`docs/LLM_AGENT_RUNTIME.md`](docs/LLM_AGENT_RUNTIME.md) — локальный Ollama, AGENT fallback, лимиты и деградация.

## Правило разработки

ARGUS остаётся инфраструктурой фактов. Классификация городских проблем, события, activity/risk, оценка спроса, парковочный потенциал и другие предметные выводы принадлежат аналитическим модулям.

Новая возможность считается реализованной только когда она работает через реальный service graph, сохраняет Evidence/Provenance, имеет определённое поведение при сбоях, ограниченные ресурсы, тесты и документацию, совпадающую с runtime.
