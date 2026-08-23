# ARGUS architecture

## Boundary

ARGUS is a server-side infrastructure service for analytical consumers such as Kraken, Janus and future modules. It is installed and supervised by the same universal Geo Analyzer module manager, but it is not a user-selectable analysis module.

The runtime manifest intentionally publishes:

- `optional=false`;
- `default_enabled=true`;
- `analysis_launch_toggle=false`;
- `capability_card=false`.

Geo Analyzer can therefore install, start, health-check, update and remove ARGUS while keeping it out of the user analysis selection UI. Consumers call the ARGUS collection API directly. ARGUS never branches on `consumer` identity.

Core boundary:

```text
ARGUS = find + obtain + prove + store
Consumers = interpret + calculate + conclude
```

## Product deployment

The repository root contains `geo-analyzer-module.json`. The current Geo Analyzer deployment manager uses it to:

1. download the pinned repository snapshot;
2. create an isolated Python virtual environment;
3. install ARGUS and Chromium;
4. provide the shared PostgreSQL DSN and a separate bearer-token file;
5. run `python -m argus.storage.cli migrate` and `check`;
6. assign a localhost API port;
7. start the ARGUS API process;
8. authenticate to `GET /v1/manifest` and `GET /v1/health`;
9. register and enable the healthy infrastructure service.

The deployed API binds only to `127.0.0.1`. PostgreSQL is mandatory for this server deployment. SQLite remains available for isolated local development and unit/integration fixtures only.

## Main collection pipeline

```text
Kraken / Janus / consumer
          |
          v
   CollectionRequest
 consumer / analysis_id
 territory / intents
 constraints / allow_partial
          |
          v
 Collection Orchestrator
          |
          +--> Research Planner
          |       |
          |       v
          |   search queries
          |       |
          |       v
          |   DiscoveryService
          |   SearXNG -> browser fallback
          |       |
          |       v
          |  destination URLs
          |
          +--> SourceRegistry
                  |
       +----------+-----------+
       |          |           |
       v          v           v
 Generic Web   RSS/Atom   map/archive adapters
       |
       v
 FAST -> BROWSER -> AGENT
       |
       +--> same-host robots.txt / Sitemap navigation
       +--> embedded JSON-LD extraction
       |
       v
 Observation + Evidence
       |
       v
 PostgreSQL Repository
       |
       +--> temporal Snapshots + diff
       +--> SiteRecipe state
       +--> recovery checkpoints
```

Discovery results, Sitemap entries, JSON-LD navigation URLs and search snippets are not automatically facts. A navigation candidate must pass through a factual source path before it can become Evidence. Embedded JSON-LD is treated as page-declared structured evidence because it is contained in the fetched page; ARGUS never dereferences a remote `@context`.

## Runtime escalation

FAST uses a persistent Crawlee HTTP runtime for static/public content. BROWSER uses a persistent Crawlee Playwright runtime for JavaScript pages. AGENT is optional and is used only when deterministic retrieval or a validated SiteRecipe cannot solve a public site.

A successful agent path does not become permanent behavior directly. Supported actions are compiled into a candidate SiteRecipe and must pass deterministic Playwright replay before the recipe is stored.

BROWSER tracks the latest main-document response during recipe navigation, so a later 403/429 cannot be hidden by an initial HTTP 200. FAST treats externally declared charsets as untrusted input and falls back safely when the declared codec is invalid.

CAPTCHA and access-control challenges are never bypassed.

## Discovery and intent isolation

`ResearchPlanner` creates search queries; `DiscoveryService` turns search hits into factual source tasks. External discovery providers are ordered fallbacks:

1. optional configured SearXNG JSON endpoint;
2. low-volume DuckDuckGo HTML through the BROWSER runtime.

Discovery is planned separately for every uncovered intent. One global query budget is shared across the collection, so multi-intent requests do not multiply search traffic without bound.

An explicit seed URL is a source candidate, not proof that all requested intents are covered. Wildcard `generic_web` therefore does not suppress discovery for other intents.

If the same destination URL is relevant to several intents, ARGUS downloads it once and merges `research_goals` into the task/provenance rather than spending page budget on duplicate fetches.

Discovery outcomes are explicit:

- normal empty providers -> `DISCOVERY_NO_RESULTS`;
- all usable discovery paths blocked -> `DISCOVERY_BLOCKED` + `DISCOVERY_INCOMPLETE`;
- exhausted global search budget -> `DISCOVERY_QUERY_BUDGET_EXHAUSTED`;
- planner emits no query for one intent -> `DISCOVERY_NO_QUERIES`.

## Recovery and idempotency

Collection execution is checkpointed persistently. Planning state includes:

- `planning_initial_tasks_complete`;
- `planning_complete`;
- `covered_intents`;
- `discovery_queries`;
- `discovery_providers`;
- `discovery_completed_intents`;
- `pending_tasks`;
- `visited`;
- `historical_branch_queries`.

The discovery checkpoint is updated after each intent. If the process stops between two intent branches, restart recovery skips completed discovery work and continues only unfinished intents.

Source tasks have stable `dedupe_key` identity. Ordinary GET tasks default to `source_id + URL`; providers that execute distinct operations against one endpoint use explicit `task_key` values. Deterministic Observation/Evidence IDs prevent duplicate facts if a crash happens between data persistence and checkpoint persistence.

## Same-host robots.txt and Sitemap discovery

`site_discovery` is internal navigation support and never counts as factual coverage itself. After a top-level HTML fetch, `generic_web` can enqueue a bounded `robots.txt` task. ARGUS also considers the conventional `/sitemap.xml` path.

Rules:

- only HTTP(S) URLs on the original hostname;
- allowed/denied domain constraints still apply;
- `.gz` sitemap files are currently skipped;
- sitemap-index fan-out and final URL counts are bounded by settings;
- every sitemap network request is a SourceTask and consumes normal `max_pages` budget;
- selected page URLs must still be fetched through `generic_web`;
- sitemap-discovered pages do not recursively start a new sitemap pass;
- missing/invalid/blocked optional Sitemap navigation is fail-open.

Sitemap and RSS/Atom XML are parsed with `defusedxml`; DTD/entity/external-reference payloads are rejected instead of expanded.

## JSON-LD structured extraction

`EmbeddedJsonLdExtractor` parses `application/ld+json` blocks already embedded in fetched HTML. It does not perform JSON-LD expansion/compaction and does not dereference remote contexts.

Parsing is bounded by block count, block size, entity count, recursion depth, container size and string size. Non-standard `NaN`/`Infinity` and recursion failures are rejected as invalid blocks. Each accepted entity becomes a separate `source_kind=json_ld` Observation/Evidence backed by the same fetched page and snapshot.

## Recursive historical research

`HistoricalBranchPlanner` is activated only for `historical_context`. It derives bounded follow-up navigation queries from already persisted factual Observation labels such as `title`, `name`, `former_name`, `old_name`, `operator` and `brand`.

Those labels are navigation hypotheses. They become facts only when a subsequent source is fetched and normalized.

Historical branching is bounded by:

- `constraints.max_depth`;
- at most three queries per expansion;
- at most twelve branch queries per collection;
- the normal page budget;
- persisted query de-duplication.

Archive-index technical observations are excluded from entity branching.

## Wayback CDX

`wayback_cdx` is optional and enabled only by configuration. ARGUS performs bounded exact-URL CDX lookup; it does not bulk crawl archive prefixes/domains.

A CDX capture row creates an `archive_capture_index` Observation/Evidence proving that a capture exists. The actual archived page is then scheduled as a normal `generic_web` task and must pass through the normal fetch, SSRF, size, snapshot and Evidence pipeline.

When `historical_context` discovery finds a current URL and Wayback is configured, ARGUS can create both a current-page task and a separate exact archive companion task.

## Maps and geocoding

`argus.maps` defines provider-neutral map contracts. The current implementation is optional OpenStreetMap Overpass. It is registered only when an endpoint is explicitly configured. No donated public Overpass endpoint is silently enabled.

For address-only map requests, an optional configured Nominatim provider can resolve coordinates. Nominatim results retain OSM attribution/ODbL provenance; factual map evidence URLs point to public OpenStreetMap rather than the configured geocoder service.

Map contracts contain factual collection output only. Competition scoring, demand interpretation, risk assessment and other consumer analytics remain outside ARGUS.

## Rate policy

Crawlee owns ordinary crawler queue/session/retry/concurrency behavior. Direct providers such as Overpass, Nominatim and Wayback use separate process-local minimum-interval gates plus a bounded 429/503 retry policy.

A valid server `Retry-After` is authoritative. If it requires waiting longer than ARGUS' configured maximum wait, ARGUS does not issue an early retry; the operation finishes as blocked/retryable provider failure for the current collection.

## Source operational health

`SourceRegistry` wraps each SourceAdapter with runtime state without creating external health probes. States are `ready`, `running`, `ok`, `degraded` and `blocked`, with timestamps and last error code.

Cancellation restores the previous stable state rather than erasing known source health.

## Storage

The collection engine depends only on the `Repository` protocol.

### Product/server

PostgreSQL is the target server backend and is mandatory in `geo-analyzer-module.json`.

ARGUS owns the dedicated PostgreSQL schema `argus`. Schema migrations are versioned and checksummed. Migration application uses a stable PostgreSQL advisory lock so concurrent installers/processes cannot apply the same migration simultaneously. Startup verifies that the installed schema version exactly matches the application expectation.

The repository uses Psycopg 3 native asyncio and an explicitly opened `AsyncConnectionPool`. The pool participates in the FastAPI service lifecycle and is closed on normal shutdown and startup failure.

### Local development

SQLite remains available through the same Repository interface for local development and isolated tests. It is not the server product storage selected by the deployment manifest.

## Module-management contract

ARGUS publishes two different contracts for two different responsibilities:

1. `geo-analyzer-module.json` — installation/supervision contract read before code execution;
2. authenticated `GET /v1/manifest` — runtime identity/capability contract checked after process startup.

`GET /v1/health` includes `protocol_version`, `module_id` and database readiness. PostgreSQL schema or connectivity failure prevents `status=ok`, so Geo Analyzer cannot register a broken installation as healthy.

ARGUS intentionally does not implement consumer analytics or expose itself as an analysis launch option. Kraken/Janus use the collection API instead of Geo Analyzer invoking ARGUS as a selected analytical module.

## Security

- localhost API bind in server manifest;
- bearer token generated and stored outside the repository;
- PostgreSQL DSN supplied through deployment secrets; secret-file value is preferred;
- secret values use Pydantic `SecretStr` and are not emitted by normal settings repr;
- HTTP(S)-only arbitrary target URLs;
- URL userinfo rejected;
- DNS-resolved loopback/private/link-local/reserved/multicast/cloud-metadata targets rejected unless explicitly allowlisted;
- redirect hops validated before FAST sends them;
- BROWSER intercepts unsafe page network destinations;
- bounded response sizes, timeouts, concurrency and rate policies;
- hardened XML and JSON-LD parsing;
- structured logs/errors redact common credential forms and URL query strings;
- CAPTCHA/access restrictions are surfaced, not bypassed.

Application SSRF validation is defense in depth. Production deployment must also enforce network egress controls.

## Provider rule

All future adapters/providers must use the common contracts, preserve provenance and stay free of consumer analytics. Product expansion means adding factual collection capability, not adding Kraken/Janus-specific branches to ARGUS core.
