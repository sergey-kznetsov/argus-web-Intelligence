# Standalone-проверка ARGUS

`argus probe` запускает одну реальную Collection без Geo Analyzer.

Probe использует обычные ARGUS contracts, source adapters, discovery, FAST/BROWSER, extractors, provenance, Observation/Evidence и CollectionOrchestrator. Отличия deployment ограничены:

```text
ARGUS_EXECUTION_ROLE=embedded
local SQLite storage
нет Geo Analyzer Module Manager
нет отдельного server worker process
```

Это инструмент factual inspection, acceptance и development diagnostics, а не production topology.

## Установка

```bash
python -m pip install -e '.[dev]'
playwright install chromium
```

Chromium нужен для BROWSER/discovery paths. Deterministic seed URL, доступный через обычный HTML FAST, может пройти без browser execution.

## Проверка конкретного source

```bash
argus probe \
  --address "Ижевск, Пушкинская, 277" \
  --intent public_mentions \
  --seed-url "https://example.org/" \
  --no-discovery \
  --max-pages 1 \
  --max-depth 0
```

Console показывает bounded summary с factual intent coverage, source coverage и previews Observation/Evidence. Полный JSON пишется в `.argus/probes/<collection_id>.json`, если `--output` не задан.

JSON report содержит:

- exact `CollectionRequest`;
- terminal CollectionRecord/checkpoint;
- complete CollectionResult;
- Observations;
- Evidence с provider/source URL;
- provenance/quality;
- acceptance state requested intents;
- canonical factual source counts;
- semantic exact-excerpt Evidence count;
- public-map providers с factual evidence;
- source health;
- operational metrics;
- elapsed time и local probe DB path.

Canonical source counting удаляет fragment/default ports/common tracking parameters, поэтому tracking URL variants не увеличивают coverage.

`--json` дополнительно печатает полный JSON в stdout.

## Strict acceptance

Terminal `completed` или большое число fetched pages не доказывает покрытие research goals. Probe использует тот же `IntentCoverageEvaluator`, что и adaptive research.

```bash
argus probe \
  --city "Ижевск" \
  --address "Пушкинская, 277" \
  --intent complaints \
  --require-covered-intents
```

Если requested intent не имеет factual coverage, report всё равно сохраняется, process exits code `2` и перечисляет uncovered intents.

Coverage evidence-aware:

- `research_goals` navigation metadata не доказательство;
- source-declared factual shape может покрыть соответствующий intent;
- exact-excerpt semantic finding учитывается только после проверки excerpt в fetched source text;
- generated text не является Evidence.

Unknown/custom intent остаётся uncovered, пока для него нет factual coverage rule.

## Address-driven discovery

```bash
argus probe \
  --city "Ижевск" \
  --address "Пушкинская, 277" \
  --intent public_mentions \
  --intent local_news \
  --max-pages 20 \
  --max-depth 2
```

Если SearXNG не настроен, текущий бесплатный discovery bootstrap при `ARGUS_BROWSER_SERP_ENABLED=true` использует `duckduckgo_fast`, `mojeek_fast` и `bing_rss` как ordered fallbacks. Старое описание только DuckDuckGo browser fallback больше не актуально.

Discovery result не Evidence, пока destination не fetched.

## Kraken mandatory coverage

Для `consumer=kraken.development.uds` + `capability=urban_signals` обычные `max-pages` semantics временно расширяются mandatory execution guard, чтобы обязательные source/map lanes не обрывались общим маленьким collection budget. После обязательного контура включается bounded optional budget. Probe report/checkpoint позволяет проверять `research_lane_coverage`.

Публичные map lanes в этом profile используют все street anchors территории. Information-only map observations не должны считаться Kraken subject messages.

## AGENT

Текущий embedded service graph, как и server graph, не подключает AGENT/LLM. Поэтому probe сейчас проверяет FAST/BROWSER и active deterministic SiteRecipe replay, но не agent-generated navigation. Старые примеры «включите AGENT env и probe его проверит» считаются устаревшими до повторного wiring в `build_services()`.

## Coordinates

```bash
argus probe \
  --latitude 56.8527 \
  --longitude 53.2115 \
  --radius-meters 1000 \
  --intent public_mentions
```

В embedded mode Nominatim, Overpass и Wayback остаются opt-in через environment. Server-role auto Overpass не применяется к standalone embedded probe.

## Domain controls

```bash
argus probe \
  --address "Ижевск" \
  --intent public_mentions \
  --allowed-domain example.org \
  --denied-domain ads.example.org
```

Используются те же URL/domain security rules.

## Полезные options

- `--output PATH` — JSON output;
- `--db-path PATH` — SQLite DB;
- `--preview-items N` — число preview items;
- `--preview-chars N` — preview text size;
- `--timeout-seconds N` — bounded diagnostic timeout;
- `--discovery / --no-discovery`;
- `--max-pages`, `--max-depth`;
- `--require-covered-intents`.

## Что проверять

1. `acceptance.requested_intents`, `covered_intents`, `uncovered_intents`, `intent_source_counts`.
2. `result.status`, `result.errors`.
3. `result.coverage` и `research_lane_coverage`, если profile его использует.
4. `observations[*].source_kind/url/data/provenance/quality`.
5. `evidence[*].source.url/text`.
6. discovery/checkpoint metadata.
7. source health и runtime metrics.

Search snippet, Sitemap row или archive index hit сами по себе не являются factual Evidence.
