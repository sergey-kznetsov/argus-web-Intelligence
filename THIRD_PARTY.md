# Сторонние компоненты

ARGUS использует поддерживаемые open-source компоненты и публичные интерфейсы вместо повторной реализации crawler-, browser- и database-инфраструктуры.

- **Crawlee for Python** — управление запросами, retries, sessions и concurrency. Apache-2.0.
- **Playwright** — BROWSER runtime и deterministic SiteRecipe replay. Apache-2.0.
- **FastAPI** — внутренний HTTP API. MIT.
- **Psycopg 3 + psycopg_pool** — PostgreSQL driver и asyncio pool. LGPL-3.0-only.
- **defusedxml** — безопасный разбор недоверенного XML. PSFL.
- **pypdf** — локальный разбор PDF. BSD-3-Clause. Текущая зависимость: `pypdf>=6.16.1,<7`.
- **SearXNG** — необязательный отдельный discovery service через HTTP API. AGPL-3.0-or-later.
- **OpenStreetMap/Overpass** — публичные геоданные ODbL с атрибуцией `© OpenStreetMap contributors`.
- **Nominatim** — необязательный HTTP geocoder, включается через `ARGUS_NOMINATIM_URL`.
- **Wayback CDX** — необязательный discovery исторических captures; содержимое найденной capture должно быть получено отдельно, прежде чем стать Evidence.

## AGENT и LLM

В репозитории сохранён код интеграции с Ollama, Browser Use и Stagehand, но текущий `build_services()` не подключает AGENT/LLM к рабочему crawler graph: создаются `agent=None` и `llm_health=None`. Legacy Ollama settings остаются читаемыми для совместимости старых environment-файлов.

Следовательно, Ollama, Browser Use и Stagehand сейчас нельзя описывать как активную часть production research pipeline. Их повторное включение требует явного подключения в service graph и отдельной проверки зависимостей, лицензий и тестов.

## Discovery

При `ARGUS_BROWSER_SERP_ENABLED=true` текущий bootstrap использует бесплатные discovery providers `duckduckgo_fast`, `mojeek_fast` и `bing_rss`. Search results служат только навигацией и не являются Evidence.

## Overpass

В `embedded` режиме Overpass остаётся opt-in. В server roles `api`/`worker`, если `ARGUS_OVERPASS_URL` не задан, ARGUS автоматически включает bounded контур с `https://overpass-api.de/api/interpreter` и fallback `https://overpass.private.coffee/api/interpreter`. Явная операторская конфигурация не перезаписывается.

ARGUS не вендорит код перечисленных проектов: используются published packages, документированные HTTP interfaces и публичные web-страницы.