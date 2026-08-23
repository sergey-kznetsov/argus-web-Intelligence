# ARGUS Web Intelligence

ARGUS is an internal evidence-first web intelligence backend. It discovers, fetches, navigates, extracts, normalizes and stores public-source data for analytical consumers such as Kraken, Janus and future modules. It does not contain their business logic and is not a Geo Analyzer checkbox/module.

Core rule: **ARGUS = find + obtain + prove + store. Consumers = interpret + calculate + conclude.**

## Current foundation

The service includes strict protocol `1.0.0` contracts, asynchronous collections, SQLite storage behind a repository abstraction, persistent Crawlee FAST/BROWSER runtimes, Generic Web and RSS/Atom adapters, Research Planner with optional local Ollama, bounded recursive historical research, ordered discovery providers, bounded `robots.txt`/Sitemap discovery, optional Wayback CDX archive lookup, agent abstraction, SiteRecipe replay/recovery, SHA-256 snapshots/diffs, Bearer authentication, redirect-aware SSRF validation, resource/rate limits, cancellation, restart checkpoints, operational source health, hardened untrusted XML parsing, structured secret-safe logging, CLI, tests and CI.

Discovery is provider-neutral. Search results, Sitemap entries and other navigation hints only seed destination URLs; snippets/navigation metadata are never treated as factual evidence. Destination pages must be fetched by ARGUS before Observation/Evidence is created. If configured providers complete normally but produce no valid destination URL, ARGUS records `DISCOVERY_NO_RESULTS` so a mixed request cannot be reported as fully complete when only some intents were covered. Fully blocked discovery without any valid destination produces `DISCOVERY_INCOMPLETE`; ARGUS never attempts to bypass the challenge.

`argus.maps` provides provider-neutral map contracts. The first actual map implementation is optional OpenStreetMap Overpass. It is registered only when `ARGUS_OVERPASS_URL` is configured, and its places enter the same collection Observation/Evidence/snapshot pipeline as web sources. Address-only map requests can additionally use an explicitly configured Nominatim geocoder. 2GIS, Yandex Maps and Google Maps remain separate future providers rather than hardcoded branches.

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

### Same-host robots.txt and Sitemap discovery

After a top-level HTML page has been fetched, ARGUS can perform a bounded best-effort site discovery pass. It checks the site's `robots.txt` for `Sitemap:` records and also tries the conventional `/sitemap.xml` path. Sitemap indexes are followed for one bounded level; `.gz` sitemap files are currently skipped.

```bash
ARGUS_SITEMAP_DISCOVERY_ENABLED=true
ARGUS_SITEMAP_MAX_URLS=20
ARGUS_SITEMAP_MAX_INDEXES=5
```

Only HTTP(S) URLs on the original hostname are accepted. Denied/allowed-domain constraints still apply. Sitemap entries are ranked using neutral territory/intent hints and only the configured number of candidates are scheduled. Sitemap tasks consume the normal collection page budget, so this path cannot silently bypass `max_pages`.

A Sitemap URL is only a navigation candidate. It becomes factual output only after `generic_web` fetches the selected page through the normal SSRF, redirect, size-limit, snapshot and Evidence pipeline. Sitemap/XML parse failures are fail-open because this feature is optional navigation support, not requested factual coverage.

RSS/Atom and Sitemap XML are parsed with `defusedxml`; entity/external-reference payloads are rejected instead of expanded. RSS Evidence points to the fetched feed URL, while an entry URL is retained separately as navigation/provenance metadata.

## Recursive historical research

The neutral `historical_context` intent enables a second research layer after factual pages have already been collected. `HistoricalBranchPlanner` can use conservative labels from Observation `title`, `name`, `former_name`, `old_name`, `operator` and `brand` fields to form follow-up search queries.

These labels are navigation hypotheses only. They do not become facts until the follow-up destination is opened and produces its own Observation/Evidence.

Historical recursion is bounded by `constraints.max_depth`, a maximum of three follow-up queries per expansion, a maximum of twelve branch queries per collection, the normal `max_pages` page budget, and checkpoint de-duplication. `historical_branch_queries` is persisted so a process restart does not repeat completed branch searches.

An empty secondary search ends that branch normally. A blocked/error secondary search degrades an otherwise useful result to `partial` rather than silently reporting full coverage.

## Optional Wayback CDX historical source

ARGUS can use a configured Wayback CDX server for exact-URL historical capture discovery. No CDX endpoint is enabled automatically.

For the Internet Archive public CDX endpoint, an operator may explicitly configure:

```bash
ARGUS_WAYBACK_CDX_URL=https://web.archive.org/cdx/search/cdx
ARGUS_WAYBACK_CAPTURE_BASE_URL=https://web.archive.org/web
ARGUS_WAYBACK_MAX_CAPTURES=5
ARGUS_WAYBACK_MIN_INTERVAL_SECONDS=2
```

The integration performs exact-URL lookups only. It requests a bounded set of unique successful captures and does not perform bulk domain/prefix crawling.

A CDX row creates `archive_capture_index` Observation/Evidence proving that the capture exists. ARGUS then schedules the concrete `.../web/<timestamp>id_/<original-url>` capture as a normal `generic_web` task. The archived page content is therefore not trusted from CDX metadata: it must pass through the same fetch, SSRF, size-limit, snapshot and Evidence pipeline as any other page.

When `historical_context` discovery finds a current URL and Wayback is configured, ARGUS automatically creates both the current-page task and an exact archive lookup companion task. Manual historical seed URLs also trigger Wayback lookup.

Archive access-control/rate blocks are surfaced as structured coverage. ARGUS does not bypass archive restrictions. A URL with no captures is treated as a normal empty archive branch, not as a collection failure.

## Optional OpenStreetMap Overpass map source

ARGUS does not enable a public Overpass endpoint automatically. Configure a self-hosted or explicitly approved interpreter endpoint:

```bash
ARGUS_OVERPASS_URL=https://overpass.example/api/interpreter
ARGUS_OVERPASS_TIMEOUT_SECONDS=30
ARGUS_OVERPASS_MIN_INTERVAL_SECONDS=1
```

When configured, `openstreetmap_overpass` is both a map provider and a factual SourceAdapter. It currently responds to neutral POI intents: `school`, `kindergarten`, `college`, `university`, `hospital`, `clinic`, `pharmacy`, `restaurant`, `cafe`, `supermarket`, `mall`, and `park`.

If the collection already contains `territory.point`, Overpass uses it directly. Otherwise an address/city can be resolved through an optional Nominatim provider:

```bash
ARGUS_NOMINATIM_URL=https://nominatim.example
ARGUS_NOMINATIM_TIMEOUT_SECONDS=15
ARGUS_NOMINATIM_MAX_RESULTS=3
ARGUS_NOMINATIM_MIN_INTERVAL_SECONDS=1
```

No public Nominatim endpoint is enabled automatically. The selected geocoding candidate is stored in provenance, but its evidence URL always points to public OpenStreetMap rather than the internal/self-hosted Nominatim service.

Overpass uses `territory.radius_meters` or a 1000 m default. Each returned place has a direct `openstreetmap.org` source URL, ODbL attribution, deterministic Observation/Evidence IDs, and a persisted snapshot of normalized map facts. Map/geocoding provider access blocks are returned as structured `blocked`/`partial` coverage rather than bypassed.

Direct Overpass/Nominatim/Wayback HTTP clients have process-local minimum interval gates in addition to remote service policies. They share a bounded 429/503 retry budget. Missing/invalid `Retry-After` uses bounded exponential delay. A valid server `Retry-After` is authoritative: if it exceeds ARGUS' configured maximum wait, ARGUS stops retrying that operation instead of issuing an early request. A self-hosted deployment can explicitly lower the corresponding `*_MIN_INTERVAL_SECONDS` where appropriate.

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

`GET /v1/capabilities` lists actually configured discovery, archive, geocoding and map providers and reports whether bounded Sitemap discovery is enabled.

`GET /v1/sources/{id}/health` distinguishes readiness from observed runtime state. Before any factual request a source reports `ready`; after success `ok`; after a partial/error result `degraded`; after an access/rate block `blocked`. The response also contains `adapter_status`, `last_attempt_at`, `last_success_at`, `last_failure_at` and `last_error_code`. Health reporting itself does not generate external probe traffic.

## Security boundary

ARGUS rejects arbitrary targets that resolve to loopback, private, link-local, multicast, reserved or cloud-metadata addresses unless an explicit internal-target allowlist permits them. FAST validates every HTTP redirect hop before the network transport sends it; BROWSER validates page network requests and blocks unsafe destinations. Response/browser time and size limits are enforced. Structured API errors and ARGUS JSON logs redact common credential forms and URL query strings.

Application-level SSRF validation is defense in depth. Production deployment must additionally restrict egress at the firewall/network layer because DNS rebinding and lower-level network behavior cannot be fully controlled by URL validation alone.

CAPTCHA and access-control challenges are not bypassed. They are reported as blocked/partial coverage.

## Agent backends

The core has an `AgentBackend` interface. Browser Use + Ollama is the default optional local-agent integration. Successful agent paths are only persisted as SiteRecipe after a reproducible Playwright replay. Stagehand support is isolated behind an optional backend because its Python/local-model integration can evolve independently of the ARGUS core. Neither paid APIs nor browser clouds are required.

## Storage and history

The current standalone runtime uses SQLite. The orchestrator depends on the repository protocol rather than SQLite directly, so PostgreSQL can be added later without rewriting collection logic.

Every successful factual collection creates persisted temporal evidence. Web documents store fetched content snapshots; map places store canonical normalized fact snapshots; archive capture-index rows store canonical capture metadata snapshots. Unchanged facts still create a temporal snapshot; changed content additionally stores a diff against the previous snapshot.

## Map provider foundation

`MapSearchRequest`, `MapPlace`, `MapSearchResult`, `MapProviderCapabilities` and `MapProviderRegistry` form the common boundary for public map providers. `MapPlace.source_url` must be a credential-free HTTP(S) source so map facts can enter the same evidence/provenance pipeline as ordinary web documents.

Map contracts contain collection facts only. Competition scoring, demand interpretation, risk assessment and other consumer analytics remain in Kraken/Janus/Historical/future modules.

## Development rules

Keep source adapters factual. No scoring, risk calculations or module-specific branches belong in ARGUS. Prefer existing open-source crawling/runtime primitives over duplicating queues, retries, sessions or concurrency management.
