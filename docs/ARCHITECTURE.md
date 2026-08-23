# ARGUS architecture

## Boundary

ARGUS `0.2.0` is a server-side infrastructure service for analytical consumers such as Kraken, Janus and future modules. It is installed and supervised by the universal Geo Analyzer module manager, but it is not a user-selectable analysis module.

The runtime manifest intentionally publishes:

- `optional=false`;
- `default_enabled=true`;
- `analysis_launch_toggle=false`;
- `capability_card=false`.

Geo Analyzer can install, start, health-check, update and remove ARGUS while keeping it out of the user analysis selection UI. Consumers call the ARGUS collection API directly. ARGUS never branches on `consumer` identity.

Core boundary:

```text
ARGUS = find + obtain + prove + store
Consumers = interpret + calculate + conclude
```

## Product deployment

The repository root contains `geo-analyzer-module.json`. The deployment manager:

1. downloads a pinned repository snapshot;
2. creates an isolated Python virtual environment;
3. installs ARGUS and Chromium;
4. provides the shared PostgreSQL DSN and a separate bearer-token file;
5. runs `python -m argus.storage.cli migrate` and `check`;
6. assigns localhost ports;
7. starts the API process and collection-worker process;
8. authenticates to `GET /v1/manifest` and `GET /v1/health`;
9. registers/enables the service only after readiness succeeds.

The production/server roles are explicit:

```text
API process     ARGUS_EXECUTION_ROLE=api
Worker process  ARGUS_EXECUTION_ROLE=worker
Local/dev       ARGUS_EXECUTION_ROLE=embedded
```

PostgreSQL is mandatory for `api` and `worker`. SQLite is reserved for embedded/local development and isolated fixtures.

The TEST Geo Analyzer manager owns its isolated module port range and remaps preferred manifest ports into that range. ARGUS therefore does not contain TEST/PRODUCTION-specific port branches.

## Server queue

The API process never executes server collections itself. It persists a queued collection and returns immediately. A worker claims persisted work from PostgreSQL.

```text
Kraken / Janus
      |
      v
POST /v1/collections
      |
      v
argus.collections(status=queued)
      |
      v
PostgreSQL worker claim
FOR UPDATE ... SKIP LOCKED
      |
      v
argus.collection_leases
      |
      v
CollectionOrchestrator.execute()
      |
      v
Observation + Evidence + Snapshots
```

`argus.worker_instances` stores worker heartbeats. `argus.collection_leases` stores exclusive collection ownership with `lease_until`.

Claim semantics:

- only `queued`/`running` collections are candidates;
- an unexpired lease excludes the collection from other workers;
- row locks use `FOR UPDATE OF c SKIP LOCKED`, so one slow/locked queue row does not block other workers;
- queued work is preferred over recovered running work;
- an expired lease can be claimed by another worker;
- each active worker renews collection leases periodically;
- if lease renewal fails, the worker cancels its local execution instead of continuing without ownership;
- collection checkpoints allow the replacement worker to resume rather than restart research blindly.

Worker startup is transactional at the service level. If PostgreSQL opens and the worker registers but its probe socket cannot bind, startup rolls back the heartbeat task, worker registration and service resources so API readiness cannot see a phantom worker.

## Readiness

`GET /v1/health` is a readiness endpoint, not a static liveness string.

For server API role it requires:

1. PostgreSQL reachable;
2. schema version equal to the application-required migration version;
3. at least one worker heartbeat newer than `ARGUS_WORKER_HEALTH_MAX_AGE_SECONDS`.

If no fresh worker exists, API health is `degraded` and `HEAD /v1/health` returns `503`. The Geo Analyzer installer therefore cannot register a deployment where only the HTTP process started but no process can execute collections.

The worker has a localhost `/readyz` probe reporting its own database readiness and active collection count. The current Geo Analyzer manager primarily gates installation through API `/v1/health`, while also failing installation if any configured process exits before readiness.

## Main collection pipeline

```text
worker lease
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
Generic Web RSS/Atom  map/archive adapters
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

Discovery results, Sitemap entries and search snippets are navigation candidates, not automatically facts. A destination must pass through a factual source path before it becomes Evidence. Embedded JSON-LD is page-declared structured evidence because it is contained in the fetched page; ARGUS never dereferences a remote `@context`.

## Idempotent submission

Server `POST /v1/collections` is idempotent at the PostgreSQL boundary.

`CollectionRequest` optionally accepts `idempotency_key`. ARGUS computes a canonical SHA-256 request fingerprint excluding that transport key.

Storage identity:

- explicit key: namespaced by `consumer` and hashed before storage;
- omitted key: derived from the canonical request fingerprint;
- the request hash is stored next to the idempotency mapping.

`argus.collection_idempotency` has a primary key on the storage idempotency key and a unique foreign key to the collection.

Creation is atomic:

1. transaction inserts a provisional collection;
2. transaction attempts `collection_idempotency` insert with `ON CONFLICT DO NOTHING`;
3. winner commits the new collection/mapping;
4. loser removes its provisional collection and loads the winner;
5. same request hash returns the existing collection;
6. different request hash raises `IdempotencyConflictError`, exposed by API as HTTP `409`.

This also covers two API processes receiving the same retry concurrently. PostgreSQL's unique constraint determines the single winner; no process-local mutex is required.

Because `analysis_id` is part of the canonical request, a new analysis naturally has a new automatic identity while transport retries of the same analysis resolve to the same collection.

## Cancellation consistency

Cross-process cancellation is persisted in PostgreSQL. `cancelled` is terminal at the storage boundary:

- a stale worker `update_collection(RUNNING)` cannot overwrite a row already marked `cancelled`;
- Observation/Evidence upserts only occur when the owning collection is not cancelled at statement time;
- worker/orchestrator additionally checks the persisted state at planning/task boundaries.

A fetch already in flight may complete before cancellation is observed. This is intentionally distinct from allowing stale state resurrection: once `cancelled` is committed, later worker state cannot turn the collection back into `running` or create new Observation/Evidence based on a later statement snapshot.

## Recovery and task identity

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

Discovery checkpoint is updated after each intent. If a worker stops between two intent branches, the replacement worker skips already completed discovery and continues unfinished intents.

Source tasks have stable `dedupe_key` identity. Ordinary GET tasks default to `source_id + URL`; providers with distinct operations against one endpoint use explicit `task_key` values. Deterministic Observation/Evidence IDs protect the crash window between result persistence and checkpoint persistence.

## Runtime escalation

FAST uses a persistent Crawlee HTTP runtime for static/public content. BROWSER uses persistent Crawlee Playwright for JavaScript pages. AGENT is optional and is used only when deterministic retrieval or a validated SiteRecipe cannot solve a public site.

A successful agent path is not persisted directly. Supported actions are compiled into a candidate SiteRecipe and must pass deterministic Playwright replay before storage.

BROWSER tracks the latest main-document response during recipe navigation, so later 403/429 status cannot be hidden by an initial 200. FAST treats externally declared charsets as untrusted input and falls back safely for invalid codec names.

CAPTCHA and access-control challenges are never bypassed.

## Discovery and intent isolation

`ResearchPlanner` creates search queries; `DiscoveryService` turns search hits into factual source tasks. External discovery providers are ordered fallbacks:

1. optional configured SearXNG JSON endpoint;
2. low-volume DuckDuckGo HTML through BROWSER.

Discovery is planned separately for each uncovered intent while sharing one collection-level query budget. An explicit seed URL is a candidate, not proof that every requested intent is covered.

When one destination is relevant to multiple intents, ARGUS fetches it once and merges `research_goals` into task/provenance context.

Discovery outcomes are explicit:

- normal empty providers -> `DISCOVERY_NO_RESULTS`;
- all usable routes blocked -> `DISCOVERY_BLOCKED` + `DISCOVERY_INCOMPLETE`;
- global search budget exhausted -> `DISCOVERY_QUERY_BUDGET_EXHAUSTED`;
- planner emitted no query -> `DISCOVERY_NO_QUERIES`.

## Same-host robots.txt and Sitemap

`site_discovery` is internal navigation support and never counts as factual coverage. After top-level HTML retrieval, `generic_web` can enqueue bounded `robots.txt`/Sitemap work.

Rules:

- HTTP(S) only;
- original hostname only;
- request domain constraints remain active;
- `.gz` sitemap files are currently skipped;
- sitemap-index and final URL fan-out are bounded;
- every sitemap request consumes normal page budget;
- selected page URLs must still pass through `generic_web`;
- sitemap-discovered pages do not recursively launch another sitemap pass;
- missing/invalid/blocked optional Sitemap navigation is fail-open.

RSS/Atom and Sitemap XML are parsed with `defusedxml`; unsafe DTD/entity payloads are rejected rather than expanded.

## JSON-LD structured extraction

`EmbeddedJsonLdExtractor` parses bounded `application/ld+json` blocks already embedded in fetched HTML. It does not expand/compact JSON-LD and never dereferences remote contexts.

Limits cover block count/size, entity count, recursion depth, container size and string size. Invalid non-standard numeric values and recursion failures are rejected. Each accepted entity gets its own `source_kind=json_ld` Observation/Evidence backed by the fetched page snapshot.

## Recursive historical research

`HistoricalBranchPlanner` activates only for `historical_context`. It derives bounded follow-up navigation queries from already persisted factual labels such as `title`, `name`, `former_name`, `old_name`, `operator` and `brand`.

Those labels are hypotheses until a later source is fetched and normalized.

Historical branching is bounded by max depth, per-expansion query count, total branch-query count, normal page budget and persisted query de-duplication. Archive-index technical observations are excluded from entity branching.

## Wayback CDX

`wayback_cdx` is optional and enabled only by configuration. ARGUS performs bounded exact-URL CDX lookup, not bulk archive prefix/domain crawling.

A CDX row creates an `archive_capture_index` Observation/Evidence proving that a capture exists. The archived page itself is separately scheduled through `generic_web` and must pass normal fetch/SSRF/size/snapshot/Evidence handling.

## Maps and geocoding

`argus.maps` defines provider-neutral contracts. Current implementation is optional OpenStreetMap Overpass. It is registered only when an endpoint is explicitly configured.

For address-only requests, optional Nominatim can resolve coordinates. Nominatim candidates retain OSM attribution/ODbL provenance; factual map evidence points to public OpenStreetMap rather than the configured geocoder endpoint.

Map contracts contain facts only. Competition scoring, demand interpretation, risk assessment and other analytics remain outside ARGUS.

## Rate policy

Crawlee owns ordinary crawler queue/session/retry/concurrency behavior. Direct providers such as Overpass, Nominatim and Wayback use separate process-local minimum-interval gates plus bounded 429/503 retry policy.

A valid server `Retry-After` is authoritative. If it exceeds ARGUS' configured maximum acceptable wait, ARGUS does not retry earlier than requested; that operation ends for the current collection instead.

## Source operational health

`SourceRegistry` wraps each SourceAdapter with runtime state without generating external health probes. States are `ready`, `running`, `ok`, `degraded` and `blocked`, with timestamps and last error code.

Cancellation restores the previous stable source status rather than erasing known health.

## Storage

The collection engine depends on the `Repository` protocol.

### Product/server PostgreSQL

ARGUS owns schema `argus`. Migrations are versioned and checksummed and are applied under a stable PostgreSQL advisory lock. Startup requires the exact expected schema version.

Current schema responsibilities:

- `collections` — authoritative collection state/body;
- `collection_idempotency` — retry identity -> collection mapping;
- `collection_leases` — exclusive worker ownership;
- `worker_instances` — worker readiness heartbeats;
- `observations` / `evidence` — factual normalized output;
- `snapshots` — temporal source state;
- `site_recipes` — deterministic site-navigation recipes.

The repository uses Psycopg 3 native asyncio and explicitly opened `AsyncConnectionPool`. Pool lifecycle is tied to process startup/shutdown and startup rollback.

### Local SQLite

SQLite implements the same core Repository contract for embedded/local development. It is not the server product storage selected by `geo-analyzer-module.json`.

## Module-management contract

ARGUS publishes two contracts for different phases:

1. `geo-analyzer-module.json` — pre-execution installation/supervision contract;
2. authenticated `GET /v1/manifest` — post-start runtime identity/capability contract.

Package version, runtime manifest version and deployment manifest version are regression-tested to remain equal.

`GET /v1/capabilities` exposes execution/storage behavior. Server API reports PostgreSQL lease queue, idempotent submission and worker-dependent readiness; embedded mode reports in-process execution.

ARGUS intentionally does not implement consumer analytics or expose itself as an analysis-launch option.

## Security

- localhost API/worker-probe binds in server manifest;
- bearer token stored outside Git;
- PostgreSQL DSN supplied through deployment secrets, preferring a secret file;
- secret values use Pydantic `SecretStr`;
- arbitrary targets limited to HTTP(S);
- URL userinfo rejected;
- resolved private/loopback/link-local/reserved/multicast/cloud-metadata targets rejected unless explicitly allowlisted;
- redirect hops validated before FAST sends them;
- BROWSER intercepts unsafe page requests;
- response/browser time, size, concurrency and rate limits;
- hardened XML and JSON-LD parsing;
- stale-worker cancellation protection in PostgreSQL;
- structured logs/errors redact common credential forms and URL query strings;
- CAPTCHA/access restrictions are surfaced, never bypassed.

Application SSRF validation is defense in depth. Production deployment must also enforce network egress controls.

## Provider rule

All future adapters/providers must use common contracts, preserve provenance and stay free of consumer analytics. Product expansion means adding factual collection capability, not adding Kraken/Janus-specific branches to ARGUS core.
