# Архитектура ARGUS

## Граница сервиса

ARGUS `0.3.0` — отдельный серверный infrastructure service для Kraken и будущих аналитических consumers Geo Analyzer. Он не устанавливается как optional module и не должен появляться в Module Manager или форме пользовательского анализа.

```text
Пользователь
  -> Geo Analyzer
  -> выбранный аналитический модуль
  -> ARGUS
  -> публичный web / открытые источники
  -> Observation + Evidence + Provenance + Coverage
  -> аналитический модуль
  -> Module Result
  -> Geo Analyzer report / UI
```

```text
Geo Analyzer = orchestrate + present
ARGUS        = find + obtain + prove + store + continue researching
Module       = normalize + interpret + calculate + conclude
```

## Consumer routing

ARGUS не должен содержать бизнес-веток вида `if consumer == "kraken"`. При этом consumer identity фактически влияет на разрешённый research contract через `ConsumerProfileRegistry` и `ToolPackRegistry`: profile выбирает capability, requested facts, planner/extractor/source policy. Это декларативная маршрутизация, а не предметная аналитика внутри Core.

## Server topology

```text
C:\argus\releases\<immutable-commit>
C:\ProgramData\ARGUS\argus.env
C:\ProgramData\ARGUS\secrets\argus.token
C:\ProgramData\ARGUS\secrets\database-dsn.txt
C:\ProgramData\ARGUS\logs
ARGUS-API
ARGUS-Worker
```

По умолчанию:

```text
API          127.0.0.1:8787
worker probe 127.0.0.1:8788
```

Роли:

```text
ARGUS_EXECUTION_ROLE=api
ARGUS_EXECUTION_ROLE=worker
ARGUS_EXECUTION_ROLE=embedded
```

Server roles требуют PostgreSQL. Текущий standalone deploy требует отдельную PostgreSQL database `argus` и service role `argus`; внутри неё ARGUS владеет schema `argus`. SQLite используется только для embedded/local mode и fixtures.

## Collection API

Health endpoints:

```text
GET  /v1/health
HEAD /v1/health
```

Authenticated endpoints:

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

`retry` endpoint в текущем API отсутствует.

## Очередь и recovery

API в server mode сохраняет queued collection и не выполняет её inline. Worker получает работу через PostgreSQL claim + lease. `worker_instances` хранит heartbeat, `collection_leases` — ownership. Lease fencing блокирует запись старого worker после передачи lease.

Успех `SourceTask` публикуется одной транзакцией: staged Snapshot, Observation, Evidence и checkpoint. Это обеспечивает at-least-once network execution без двойной durable публикации одного успешно зафиксированного task.

## Readiness

Для role=`api` `/v1/health` считается ready только если PostgreSQL доступен и есть свежий heartbeat хотя бы одного worker. Иначе status=`degraded`.

## Research pipeline

Текущий runtime:

```text
CollectionOrchestrator
  -> consumer profile / tool pack
  -> Research Planner
  -> DiscoveryService
  -> SourceRegistry
  -> FAST
  -> BROWSER при необходимости
  -> extractor / normalizer
  -> ConsumerDeliveryProjector
  -> Observation + Evidence + Provenance
  -> PostgreSQL
  -> bounded follow-up / coverage checks
```

AGENT-код существует, но текущий `build_services()` его не подключает. Поэтому production pipeline нельзя документировать как `FAST -> BROWSER -> AGENT` до отдельного повторного включения.

## Kraken urban_signals

Для `kraken.development.uds` profile v1 используется capability `urban_signals`. Mandatory orchestrator запускает обязательные source contours, затем публичные map lanes и только после них bounded optional research. Обязательные линии не пропускаются из-за seed coverage или recovery checkpoint.

Публичные map lanes проходят все street anchors, попавшие в радиус, по каждому provider. Их information-only observations не выдаются Kraken как обычные subject messages; Evidence сохраняется для контекста/проверяемости. Состояние обязательных линий доступно в `research_lane_coverage`.

## Sources

В bootstrap фактически регистрируются `generic_web`, `mingkh_residential`, `pastvu_historical`, `rss_atom`, `json_feed`, `site_discovery`, `openstreetmap_overpass` при наличии map config и `wayback_cdx` при включённом Wayback.

Discovery providers: optional SearXNG и, при `browser_serp_enabled`, `duckduckgo_fast`, `mojeek_fast`, `bing_rss`.

В server roles Overpass автоматически получает bounded бесплатный endpoint, если оператор не задал свой. Nominatim и Wayback остаются opt-in.

## Evidence и даты

Search snippets, Sitemap rows, discovery metadata и navigation hints не являются Evidence. Publication time заполняется только из source-backed данных; неизвестная дата остаётся `null`, а `collected_at` хранится отдельно.

## Storage

ARGUS владеет только своими объектами. Migrations versioned/checksummed и защищены advisory lock. Backup/restore/retention работают в границах ARGUS database/schema и не должны затрагивать Geo Analyzer.

## Free contour

Base runtime не должен требовать платных SERP, proxy networks, CAPTCHA solvers, browser clouds, Google/Yandex/2GIS API или cloud LLM. Допустимы public HTML/endpoints, RSS, files, open data, open-source runtimes и self-hosted/free services.

## Критерий готовности

Функция считается реализованной, когда она проходит через реальный service graph, сохраняет Evidence/Provenance, имеет bounded resource/recovery поведение, тесты и актуальную документацию. Наличие не подключённого класса или экспериментального adapter само по себе не означает готовую production capability.
