# ARGUS Web Intelligence — продуктовый принцип

Статус: нормативное определение назначения ARGUS. Этот документ описывает границы продукта и одновременно отделяет целевое направление от фактически включённых runtime-возможностей.

## 1. Назначение

ARGUS — универсальный backend исследования публичного web для экосистемы Geo Analyzer.

Он получает территорию и research contract, находит публичные источники, получает материал, извлекает source-backed факты, сохраняет Evidence/Provenance, продолжает bounded исследование по найденным сущностям и возвращает структурированный factual corpus вызывающему модулю.

```text
ARGUS = find + obtain + prove + store + continue researching
Module = interpret + calculate + conclude
```

ARGUS не пишет итоговый предметный отчёт, не рассчитывает risk/parking/demand и не принимает бизнес-решения за consumer module.

## 2. Положение в системе

```text
Geo Analyzer
  -> analytical modules
  -> ARGUS
  -> public internet / maps / archives / portals / documents
```

ARGUS — один standalone server service. Его lifecycle принадлежит серверному deployment, а не Module Manager Geo Analyzer.

Общий consumer contract:

```text
ARGUS_SERVICE_BASE_URL=http://127.0.0.1:8787
ARGUS_SERVICE_TOKEN_FILE=C:\ProgramData\ARGUS\secrets\argus.token
```

## 3. Consumer profiles

Consumer передаёт stable `consumer`, profile version, capability, requested facts, territory, intents и constraints. ARGUS использует декларативные registries/tool packs для выбора разрешённого исследовательского контура. В Core не должно появляться бизнес-условий по названию модуля.

## 4. Исследование территории

При radius-анализе ARGUS не ограничивается одним исходным домом. Он должен использовать доказанные entity/street anchors внутри территории и запускать bounded дополнительные ветки. Для текущего Kraken `urban_signals` обязательный контур проходит все обязательные research lanes, включая street scope, до bounded optional research.

Recursive branch остаётся гипотезой, пока отдельный source fetch не создаст Evidence.

## 5. LLM/AGENT

Локальный Ollama включён как необязательный управляющий слой с детерминированным fallback. Он участвует в initial planning, follow-up, supervision, source-grounded entity hypotheses, семантической классификации точных цитат и AGENT-навигации. Модель может предлагать только bounded research/navigation actions; её factual output не принимается.

При `ARGUS_LLM_REQUIRED=false` недоступность Ollama не останавливает API/worker и не отменяет детерминированный сбор. Все модельные компоненты одного worker используют общий последовательный LLM gate.

## 6. Retrieval

Фактическая эскалация:

```text
verified SiteRecipe replay
  -> FAST
  -> BROWSER, если FAST недостаточен
  -> AGENT, если BROWSER завершился ошибкой или дал недостаточный незаблокированный DOM
  -> deterministic BROWSER replay
  -> factual extraction
```

В режиме auto AGENT backends пробуются строго как Recipe → Stagehand → Browser Use. CAPTCHA/login/paywall/access-control не обходятся. Recipe активируется только после replay и source-backed проверки цели.

## 7. Источники и форматы

ARGUS поддерживает generic web и специализированные source adapters. В репозитории фактически реализованы bounded extraction paths для HTML, RSS/Atom, JSON Feed, Sitemap navigation, PDF, CSV/TSV/JSON/XML, gzip structured files, DOCX/XLSX, HTML tables, JSON-LD/Microdata/page metadata, GeoJSON, KML/KMZ, snapshots/Wayback/PastVu и OSM/Overpass.

Наличие source catalogue не означает наличие dedicated adapter для каждого перечисленного сайта.

## 8. Исторический контур

История строится из source-backed captures/snapshots и public archive sources. ARGUS может фиксировать page/entity changes между подтверждёнными captures, но не должен выдумывать единую историю при конфликтующих или неполных данных.

## 9. Evidence rule

Каждый factual Observation должен быть связан с Evidence/Provenance. Search snippet, query, navigation score, Sitemap row, model text или сам факт нахождения URL не являются предметным доказательством.

Неизвестная publication date остаётся `null`.

## 10. Free base contour

Базовый продукт работает без обязательных платных search APIs, proxies, CAPTCHA solving, commercial browser clouds, Google/Yandex/2GIS APIs и paid LLM.

## 11. Recovery

Server storage — PostgreSQL database/schema ARGUS; local embedded — SQLite. Collection state, observations, evidence, snapshots, checkpoints и leases должны переживать restart и обеспечивать replay-safe продолжение.

## 12. Standalone verification

`argus probe` остаётся инструментом проверки реального embedded service graph без Geo Analyzer. Он не должен подменять production topology, но обязан использовать те же planners/adapters/extractors/contracts.

## 13. Definition of done

Capability готова только когда:

1. работает end-to-end через реальный runtime;
2. factual output имеет Evidence/Provenance;
3. определены failure/recovery semantics;
4. есть явные limits;
5. важный путь покрыт tests/probe;
6. документация описывает именно текущую реализацию.

Неподключённый код, будущий adapter или нормативное пожелание должны быть явно названы как planned/dormant, а не как готовая функция.
