# ARGUS Web Intelligence

ARGUS is an internal evidence-first web intelligence backend. It discovers, fetches, navigates, extracts, normalizes and stores public-source data for analytical consumers such as Kraken, Janus and future modules. It does not contain their business logic and is not a Geo Analyzer checkbox/module.

Core rule: **ARGUS = find + obtain + prove + store. Consumers = interpret + calculate + conclude.**

## Current foundation

The service includes strict protocol `1.0.0` contracts, asynchronous collections, SQLite storage behind a repository abstraction, persistent Crawlee FAST/BROWSER runtimes, Generic Web and RSS/Atom adapters, Research Planner with optional local Ollama, recursive research tasks, ordered discovery providers, agent abstraction, SiteRecipe replay/recovery, SHA-256 snapshots/diffs, Bearer authentication, redirect-aware SSRF validation, resource/rate limits, cancellation, restart checkpoints, structured secret-safe logging, CLI, tests and CI.

Discovery is provider-neutral. Search results only seed destination URLs; snippets are never treated as factual evidence. Destination pages must be fetched by ARGUS before Observation/Evidence is created.

`argus.maps` provides provider-neutral map contracts. The first actual map implementation is optional OpenStreetMap Overpass. It is registered only when `ARGUS_OVERPASS_URL` is configured, and its places enter the same collection Observation/Evidence/snapshot pipeline as web sources. 2GIS, Yandex Maps and Google Maps remain separate future providers rather than hardcoded branches.

## Install

Python 3.11+ is required. Python 3.12 is used in CI.

```bash
python -m venv .venv
. .venv/bin/activate              # Windows: .venv\\Scripts\\activate
pip install -e '.[dev]'
playwright install chromium
```

Create a token once:

```bash
argus init-token
```

Run:

```bash
argus serve
```

By default ARGUS binds to `127.0.0.1:8787`.

## CLI

```bash
argus collect --consumer test --address "Ижевск, Пушкинская, 277" --intent public_mentions
argus collect --consumer test --address "Ижевск, Пушкинская, 277" --intent public_mentions --seed-url https://example.org/
argus status <collection_id>
argus result <collection_id>
argus sources
```

With browser SERP discovery enabled (default), a collection can start from territory + intents without a seed URL. If a self-hosted SearXNG is configured it is tried first; the low-volume Playwright DuckDuckGo HTML provider is only a fallback. If every discovery path is blocked by an anti-bot challenge, ARGUS reports `blocked` and does not attempt a bypass.

All API endpoints except `/v1/health` require `Authorization: Bearer <token>`.

## Free discovery

### Preferred: self-hosted SearXNG

ARGUS does not bundle or import SearXNG. Run an unmodified/self-hosted SearXNG separately and enable JSON in its `search.formats` configuration. For example:

```yaml
search:
  formats:
    - html
    - json
```

Then configure ARGUS:

```bash
ARGUS_SEARXNG_URL=http://127.0.0.1:8888
ARGUS_DISCOVERY_MAX_QUERIES=8
ARGUS_SEARXNG_MAX_RESULTS_PER_QUERY=10
```

The provider uses SearXNG's documented `/search` HTTP API with POST and `format=json`. Public SearXNG instances are not assumed to expose JSON; a local/private instance is the intended baseline.

### Fallback: DuckDuckGo HTML through Playwright

The fallback uses the existing BROWSER runtime and submits DuckDuckGo's public no-JS HTML search form. It is intentionally low volume and does not reverse-engineer or call private search APIs.

```bash
ARGUS_BROWSER_SERP_ENABLED=true
ARGUS_BROWSER_SERP_MAX_RESULTS_PER_QUERY=5
ARGUS_BROWSER_SERP_WAIT_MS=750
```

Disable it with `ARGUS_BROWSER_SERP_ENABLED=false` when only explicitly configured discovery providers are desired.

## Optional OpenStreetMap Overpass map source

ARGUS does not enable a public Overpass endpoint automatically. Configure a self-hosted or explicitly approved interpreter endpoint:

```bash
ARGUS_OVERPASS_URL=https://overpass.example/api/interpreter
ARGUS_OVERPASS_TIMEOUT_SECONDS=30
```

When configured, `openstreetmap_overpass` is both a map provider and a factual SourceAdapter. It currently responds to neutral POI intents: `school`, `kindergarten`, `college`, `university`, `hospital`, `clinic`, `pharmacy`, `restaurant`, `cafe`, `supermarket`, `mall`, and `park`.

The collection request must include `territory.point`; the adapter uses `territory.radius_meters` or a 1000 m default. Each returned place has a direct `openstreetmap.org` source URL, ODbL attribution, deterministic Observation/Evidence IDs, and a persisted snapshot of normalized map facts. Map-source rate limits/access blocks are returned as structured `blocked`/`partial` coverage rather than bypassed.

## API

- `GET /v1/health`
- `GET /v1/capabilities`
- `POST /v1/collections`
- `GET /v1/collections/{collection_id}`
- `GET /v1/collections/{collection_id}/result`
- `POST /v1/collections/{collection_id}/cancel`
- `GET /v1/sources`
- `GET /v1/sources/{source_id}/health`

`POST /v1/collections` returns `202 Accepted`. Work continues asynchronously and survives process restarts through persisted task/checkpoint state. Reprocessing the same source content within the same collection uses deterministic Observation/Evidence identities to avoid duplicates after a crash/restart window.

## Security boundary

ARGUS rejects arbitrary targets that resolve to loopback, private, link-local, multicast, reserved or cloud-metadata addresses unless an explicit internal-target allowlist permits them. FAST validates every HTTP redirect hop before the network transport sends it; BROWSER validates page network requests and blocks unsafe destinations. Response/browser time and size limits are enforced. Structured API errors and ARGUS JSON logs redact common credential forms and URL query strings.

Application-level SSRF validation is defense in depth. Production deployment must additionally restrict egress at the firewall/network layer because DNS rebinding and lower-level network behavior cannot be fully controlled by URL validation alone.

CAPTCHA and access-control challenges are not bypassed. They are reported as blocked/partial coverage.

## Agent backends

The core has an `AgentBackend` interface. Browser Use + Ollama is the default optional local-agent integration. Successful agent paths are only persisted as SiteRecipe after a reproducible Playwright replay. Stagehand support is isolated behind an optional backend because its Python/local-model integration can evolve independently of the ARGUS core. Neither paid APIs nor browser clouds are required.

## Storage and history

The current standalone runtime uses SQLite. The orchestrator depends on the repository protocol rather than SQLite directly, so PostgreSQL can be added later without rewriting collection logic.

Every successful factual collection creates persisted temporal evidence. Web documents store fetched content snapshots; map places store canonical normalized fact snapshots. Unchanged facts still create a temporal snapshot; changed content additionally stores a diff against the previous snapshot.

## Map provider foundation

`MapSearchRequest`, `MapPlace`, `MapSearchResult`, `MapProviderCapabilities` and `MapProviderRegistry` form the common boundary for public map providers. `MapPlace.source_url` must be a credential-free HTTP(S) source so map facts can enter the same evidence/provenance pipeline as ordinary web documents.

Map contracts contain collection facts only. Competition scoring, demand interpretation, risk assessment and other consumer analytics remain in Kraken/Janus/Historical/future modules.

## Development rules

Keep source adapters factual. No scoring, risk calculations or module-specific branches belong in ARGUS. Prefer existing open-source crawling/runtime primitives over duplicating queues, retries, sessions or concurrency management.
