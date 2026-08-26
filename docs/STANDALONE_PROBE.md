# Standalone ARGUS probe

`argus probe` runs one real ARGUS collection without Geo Analyzer.

It uses the normal ARGUS service graph, source adapters, discovery, FAST/BROWSER escalation, extractors, provenance, Evidence/Observation models and collection orchestrator. The deployment substitutions are intentionally limited to:

- `ARGUS_EXECUTION_ROLE=embedded`;
- local SQLite storage;
- no external Geo Analyzer module manager;
- no separate collection worker process.

This mode is for factual inspection, acceptance checks and development diagnostics. It is not the product server topology.

## Installation

```bash
python -m pip install -e '.[dev]'
playwright install chromium
```

Chromium is needed when browser discovery or BROWSER fallback is used. A deterministic seed-URL probe that stays on a normal server-rendered page can use FAST without browser execution.

## Deterministic source test

Use an explicit public page and disable discovery when you want to inspect exactly what ARGUS extracts from that page:

```bash
argus probe \
  --address "Ижевск, Пушкинская, 277" \
  --intent public_mentions \
  --seed-url "https://example.org/" \
  --no-discovery \
  --max-pages 1 \
  --max-depth 0
```

The console prints a bounded summary with factual intent coverage, source coverage plus Observation and Evidence previews. The full JSON is written under `.argus/probes/<collection_id>.json` unless `--output` is supplied.

The JSON report contains:

- the exact `CollectionRequest`;
- terminal `CollectionRecord` and checkpoint state;
- complete `CollectionResult`;
- every Observation;
- every Evidence item with provider and source URL;
- provenance and quality metadata;
- evidence-aware acceptance state for every requested intent;
- count of distinct factual source URLs supporting each requested intent;
- semantic exact-excerpt Evidence count;
- public-map providers that actually produced factual observations;
- source health state;
- operational metrics for the run;
- elapsed time and local probe database path.

To also print the complete JSON to stdout, add `--json`.

## Strict acceptance mode

A collection reaching `completed` or producing many pages does not by itself prove that the requested research goals were satisfied. The probe therefore evaluates final observations with the same `IntentCoverageEvaluator` used by adaptive follow-up research.

Use `--require-covered-intents` when the command should fail unless every requested intent has factual coverage:

```bash
argus probe \
  --city "Ижевск" \
  --address "Пушкинская, 277" \
  --intent reviews \
  --intent complaints \
  --require-covered-intents
```

The report is always written first. If one or more requested intents remain uncovered, the command exits with code `2` and names those intents. This makes `argus probe` suitable for repeatable smoke/acceptance scripts without confusing successful navigation with successful research.

Coverage is evidence-aware:

- `research_goals` navigation metadata never counts as proof;
- source-declared factual shapes such as `Review` may satisfy the corresponding intent;
- exact-excerpt semantic findings may satisfy supported semantic intents only after the excerpt is verified against fetched source text;
- model-generated text never counts as Evidence.

For future/custom intents that the current coverage evaluator does not know how to prove, strict mode will correctly leave them uncovered until a factual coverage rule is implemented.

## Address-driven discovery test

To test how ARGUS searches for sources from a location rather than from a known URL:

```bash
argus probe \
  --city "Ижевск" \
  --address "Пушкинская, 277" \
  --intent public_mentions \
  --intent local_news \
  --max-pages 20 \
  --max-depth 2
```

With no configured SearXNG endpoint, the current free discovery fallback is DuckDuckGo browser discovery. This requires installed Chromium. Discovery hits are navigation hints only; they do not become Evidence until ARGUS fetches the destination page.

## Public-map acceptance test

To exercise the free public-web map path, request one or more map-specific factual intents such as `reviews` or `complaints` and keep discovery enabled:

```bash
argus probe \
  --city "Ижевск" \
  --address "Пушкинская, 277" \
  --intent reviews \
  --intent complaints \
  --max-pages 20 \
  --max-depth 2 \
  --require-covered-intents
```

The acceptance block reports only map providers that actually produced factual observations with `public_map_source` provenance. Merely discovering or opening Yandex Maps, 2GIS or Google Maps does not add a provider to this list.

When AGENT is deliberately enabled in environment configuration, the same probe also exercises deterministic public review views, bounded semantic AGENT rounds and verified SiteRecipe replay. A CAPTCHA/access block remains a blocked source and is not bypassed.

## Coordinates

```bash
argus probe \
  --latitude 56.8527 \
  --longitude 53.2115 \
  --radius-meters 1000 \
  --intent public_mentions
```

Nominatim, Overpass and Wayback remain opt-in provider endpoints and are configured with the same environment variables as normal ARGUS. The probe does not silently enable third-party endpoints that product configuration has not enabled.

## Domain controls

```bash
argus probe \
  --address "Ижевск" \
  --intent public_mentions \
  --allowed-domain example.org \
  --denied-domain ads.example.org
```

The same ARGUS URL safety and domain constraints apply in standalone mode.

## Useful options

- `--output PATH` — choose the JSON report path;
- `--db-path PATH` — choose the local SQLite database;
- `--preview-items N` — number of Observation/Evidence items shown in the console;
- `--preview-chars N` — text preview size;
- `--timeout-seconds N` — stop a hung diagnostic collection;
- `--discovery / --no-discovery` — enable or disable discovery;
- `--max-pages` and `--max-depth` — use the normal collection budgets;
- `--require-covered-intents` — exit `2` when any requested intent lacks factual coverage.

## What to inspect

For every probe, verify at minimum:

1. `acceptance.requested_intents`, `covered_intents`, `uncovered_intents` and `intent_source_counts`;
2. `result.status` and `result.errors`;
3. `result.coverage` to see which factual adapters actually ran;
4. `result.observations[*].source_kind`, `url`, `data`, `provenance`, and `quality`;
5. `result.evidence[*].source.url` and `text` to confirm the observation is backed by the fetched source;
6. `collection.checkpoint.discovery_queries` / provider metadata when discovery was used;
7. source health and runtime metrics for blocked, degraded, retried or escalated paths.

A search snippet, sitemap row or archive index hit is not factual Evidence by itself. The destination content must be fetched before it can support an Observation.
