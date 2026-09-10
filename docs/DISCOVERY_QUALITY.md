# Качество discovery и политика дубликатов

ARGUS рассматривает discovery как навигацию, а не как доказательство. Search providers, provider rank, snippets, canonical URL cleanup, locality match и navigation score определяют только порядок получения публичных destination. Фактом destination становится после fetch и формирования Observation + Evidence.

## Планирование запросов

Количество discovery queries ограничено `discovery_max_queries`; текущее значение по умолчанию — `12`. Heuristic planner распределяет бюджет round-robin между requested intents, чтобы один intent не съел весь query budget. Запросы нормализуются, дедуплицируются и ограничиваются по длине.

Старое упоминание Ollama planner как активного runtime больше не актуально: текущий `build_services()` не подключает LLM/AGENT. Если LLM planner будет возвращён, его output должен проходить те же count/length limits и deterministic fallback.

## Canonical navigation identity

Для обычных HTTP(S) navigation tasks используется `discovery-url-identity/1`.

Нормализация консервативная:

- удаляется fragment;
- hostname нормализуется, включая IDNA;
- default ports удаляются;
- известные tracking parameters (`utm_*` и аналогичные) удаляются;
- остальные query parameters и path semantics сохраняются.

Factual source URL остаётся реально fetched URL. Source-declared canonical URL или ARGUS navigation identity не заменяют URL, из которого получен Evidence.

## Discovery ranking

`discovery-ranking/1` сортирует destinations детерминированно:

1. priority из `allowed_domains`;
2. provider rank;
3. совпадения locality tokens из city/address;
4. HTTPS как tie-break;
5. canonical URL как стабильный последний tie-break.

`discovery_navigation_score` — только crawl-order score. Это не source reliability, factual confidence или Evidence quality.

Navigation metadata может попасть в factual provenance только с явной маркировкой:

```text
navigation_only=true
is_evidence=false
```

## Stop conditions и budgets

Обычный discovery использует `first_provider_with_valid_destinations`: после первого provider, который дал валидные публичные destinations, следующие fallback providers для этого discovery call не вызываются.

Tasks одного discovery call ограничены `CollectionRequest.constraints.max_pages`. Wayback companion tasks делят тот же budget с live destination tasks и не удваивают его. Если остаётся одно место, приоритет получает live factual fetch.

`DiscoveryOutcome` может содержать stop reason:

```text
no_queries
first_provider_with_valid_destinations
task_budget_reached
blocked_without_destinations
no_valid_destinations
providers_exhausted
```

Это operational/navigation telemetry, не Evidence.

Для Kraken `urban_signals` поверх общего discovery действует отдельный обязательный `mandatory-coverage/5`: source contours и public-map lanes выполняются независимо от seed intent coverage, поэтому этот special planner policy нельзя описывать как обычный one-provider discovery flow.

## Exact duplicate-content suppression

После фактического fetch и normalization ARGUS выполняет collection-scoped exact content deduplication по `committed-content-hash/1`.

Дубликаты ищутся по committed storage, а не process-local cache. Поэтому task с неуспешным atomic commit не может отравить duplicate state; recovery worker безопасно повторяет его.

Политика намеренно консервативна:

- участвуют primary document representations: `web_page`, `pdf_document`, `structured_data`, `office_document`, `office_spreadsheet`, `office_document_file`;
- nested JSON-LD/Microdata/GeoJSON/KML child facts не используются для suppression всей страницы;
- для HTML `web_page` нужно минимум 256 normalized text chars;
- duplicate Observation и его Evidence сохраняются;
- подавляются только новые navigation tasks и historical branching из точного duplicate;
- provenance сохраняет `duplicate_of` с original committed Observation/URL.

ARGUS сейчас не делает fuzzy/semantic/near-duplicate classification и не дедуплицирует контент между разными collections. Это исключено намеренно, чтобы не объединять разные evidence без достаточно сильной deterministic identity.
