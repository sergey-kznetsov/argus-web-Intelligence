# ARGUS Web Intelligence

ARGUS `0.2.0` is a server-side evidence-first web intelligence backend for Kraken, Janus and future analytical consumers in the Geo Analyzer ecosystem.

Core rule: **ARGUS = find + obtain + prove + store. Consumers = interpret + calculate + conclude.**

ARGUS is installed and supervised by the universal Geo Analyzer server-module manager, but it is intentionally hidden from the user analysis selection UI. Its runtime manifest sets `analysis_launch_toggle=false`; consumers call the ARGUS collection API directly.

## Current product architecture

ARGUS currently provides:

- protocol `1.0.0` CollectionRequest/CollectionResult contracts;
- separate server API and collection-worker processes;
- PostgreSQL-backed queue with worker heartbeat and per-collection leases;
- atomic queue claim through `FOR UPDATE ... SKIP LOCKED`;
- FIFO recovery so interrupted older work is not starved by new submissions;
- lease recovery after worker/process failure;
- idempotent server collection submission with a bounded 24-hour default retry window;
- atomic queue admission/backpressure across concurrent API processes;
- authenticated operational queue metrics;
- bounded automatic PostgreSQL retention under a shared maintenance lock;
- terminal PostgreSQL cancellation that stale workers cannot overwrite;
- persistent research/discovery checkpoints for restart recovery;
- product PostgreSQL storage in dedicated schema `argus`;
- local/dev SQLite backend through the same Repository contract;
- versioned/checksummed PostgreSQL migrations protected by an advisory lock;
- Psycopg 3 native async pool with startup schema verification and lifecycle shutdown;
- persistent Crawlee FAST and Playwright BROWSER runtimes;
- optional local AGENT escalation through Browser Use/Ollama;
- deterministic SiteRecipe replay before recipe persistence;
- SearXNG discovery with DuckDuckGo HTML browser fallback;
- Generic Web, RSS/Atom, Sitemap, Wayback CDX and OpenStreetMap/Overpass factual paths;
- optional Nominatim geocoding;
- bounded recursive historical research;
- embedded bounded JSON-LD extraction without remote `@context` dereferencing;
- Observation + Evidence + provenance + SHA-256 temporal snapshots/diffs;
- deterministic task/Observation/Evidence identities;
- per-source operational health;
- redirect-aware SSRF defenses, Bearer authentication, resource/rate limits and secret-safe logging;
- GitHub CI configured with a PostgreSQL service.

Discovery results and navigation hints are not facts. Search snippets, Sitemap entries and archive navigation metadata only seed factual retrieval. A destination page must be fetched before it can become page Evidence. Embedded JSON-LD is evidence only because it is contained in the already fetched page.

## Server deployment through Geo Analyzer

The repository root contains `geo-analyzer-module.json`. The universal manager can install ARGUS without ARGUS-specific branches in Geo Analyzer.

The deployment contract requires:

- isolated Python virtual environment;
- Chromium installation for Playwright;
- shared PostgreSQL supplied through `GEOANALYZER_DATABASE_DSN` / `GEOANALYZER_DATABASE_DSN_FILE`;
- ARGUS migrations and schema check before process startup;
- a separate generated Bearer token file through `ARGUS_TOKEN_FILE`;
- localhost-only API and worker-probe ports;
- one API process with `ARGUS_EXECUTION_ROLE=api`;
- one collection worker with `ARGUS_EXECUTION_ROLE=worker`;
- authenticated `/v1/manifest` and `/v1/health` checks;
- automatic registration/enablement only after readiness succeeds.

The API process is not considered ready merely because its HTTP socket is open. In server mode `/v1/health` requires both a healthy PostgreSQL schema and at least one fresh worker heartbeat. If the worker does not start, dies, or becomes stale, readiness degrades and `HEAD /v1/health` returns `503`.

The runtime manifest identifies the service as `argus.web.intelligence` and publishes:

```json
{
  "ui": {
    "optional": false,
    "default_enabled": true,
    "analysis_launch_toggle": false,
    "capability_card": false
  }
}
```

Therefore ARGUS is manageable as a server module but does not appear as a checkbox in «Новый анализ».

## Server queue and recovery

Server API and collection execution are intentionally separated:

```text
Kraken / Janus
      |
      v
POST /v1/collections
      |
      v
PostgreSQL queued collection
      |
      v
worker claim + lease
      |
      v
CollectionOrchestrator
      |
      v
Observation / Evidence / snapshots
```

Workers register in `argus.worker_instances`. A collection is claimed through `argus.collection_leases`; concurrent workers skip already locked/leased work. The worker periodically renews both its own heartbeat and each active collection lease. An expired lease allows another worker to resume the persisted collection checkpoint after a crash.

Claim ordering is FIFO by collection creation time regardless of whether a record is freshly `queued` or recovered `running`. This prevents interrupted older work from being permanently displaced by a continuous stream of new requests.

The configurable defaults are:

```bash
ARGUS_WORKER_CONCURRENCY=2
ARGUS_WORKER_POLL_INTERVAL_SECONDS=1
ARGUS_WORKER_LEASE_SECONDS=90
ARGUS_WORKER_HEARTBEAT_SECONDS=20
ARGUS_WORKER_HEALTH_MAX_AGE_SECONDS=60
```

`ARGUS_WORKER_HEARTBEAT_SECONDS` must be shorter than the lease duration.

Cancellation is terminal in PostgreSQL. If API records `cancelled`, a stale worker cannot change the collection back to `running`; Observation/Evidence writes that start after the cancellation is visible are rejected by the storage layer. A network operation already in flight may finish before the worker observes cancellation, but it cannot resurrect the collection state.

## Idempotent collection submission

`POST /v1/collections` is idempotent in server API mode.

A consumer may supply an explicit `idempotency_key`:

```json
{
  "protocol_version": "1.0.0",
  "consumer": "kraken",
  "analysis_id": "analysis-id",
  "idempotency_key": "kraken-analysis-id-attempt-1",
  "territory": {
    "city": "Ижевск",
    "address": "Ижевск, Пушкинская, 277"
  },
  "intents": ["public_mentions", "local_news"],
  "constraints": {
    "max_pages": 30,
    "max_depth": 2
  },
  "allow_partial": true
}
```

Rules:

- retrying the same request with the same explicit key inside the configured window returns the original `collection_id`;
- an explicit key is scoped by `consumer`, so independent consumers may reuse the same literal key;
- reusing one consumer's explicit key for a different request inside the window returns HTTP `409`;
- blank/whitespace-only keys are rejected by request validation;
- when the key is omitted, ARGUS builds a SHA-256 fingerprint from the canonical request and uses it as the retry identity;
- the transport `idempotency_key` itself is excluded from the request fingerprint;
- the default idempotency window is 86,400 seconds (24 hours);
- after an idempotency mapping expires, the same request/key may intentionally create a fresh collection.

```bash
ARGUS_IDEMPOTENCY_WINDOW_SECONDS=86400
```

Because `analysis_id` is part of the request fingerprint, a genuinely new consumer analysis should use a new analysis ID. Network retries of the same analysis resolve to the same collection rather than creating duplicate web work.

## Queue admission and operations

New server collections are admitted under a PostgreSQL transaction-level advisory lock. This makes the count-and-insert decision atomic even when several API processes submit concurrently.

Defaults:

```bash
ARGUS_QUEUE_MAX_ACTIVE_COLLECTIONS=500
ARGUS_QUEUE_MAX_ACTIVE_PER_CONSUMER=100
ARGUS_QUEUE_RETRY_AFTER_SECONDS=15
```

Semantics:

- an already-admitted idempotent retry bypasses capacity rejection and returns the existing collection;
- a new request above the per-consumer limit returns HTTP `429` with `Retry-After`;
- a new request above the global active limit returns HTTP `503` with `Retry-After`.

Authenticated `GET /v1/operations/queue` exposes PostgreSQL-derived operational state:

- queued and running counts;
- active and expired collection leases;
- active and stale worker registrations;
- age in seconds of the oldest queued/running collection;
- configured active-queue limits and idempotency window.

The endpoint is intentionally JSON and does not create a second metrics datastore. It can be consumed directly by Geo Analyzer admin tooling and later exported into Prometheus/OpenTelemetry without changing the queue source of truth.

## Retention

Server workers run bounded retention passes automatically. A PostgreSQL advisory lock ensures only one worker performs a retention pass at a time even if ARGUS is horizontally scaled.

Defaults:

```bash
ARGUS_RETENTION_MAINTENANCE_INTERVAL_SECONDS=3600
ARGUS_RETENTION_COLLECTION_DAYS=180
ARGUS_RETENTION_SNAPSHOT_DAYS=365
ARGUS_RETENTION_BATCH_SIZE=500
```

Retention rules:

- `queued` and `running` collections are never deleted by retention;
- only terminal `completed`, `partial`, `blocked`, `failed` and `cancelled` collections older than the configured collection retention are removed;
- Observation/Evidence/idempotency/lease rows tied to a removed collection follow PostgreSQL foreign-key cleanup;
- expired idempotency mappings are removed independently once their retry window has elapsed;
- old snapshots are deleted in bounded batches, but the newest snapshot for every `source_url` is always retained as the future diff baseline;
- SiteRecipe records are not automatically purged;
- snapshot retention cannot be configured shorter than collection retention.

## Storage

### Server/product

Server deployment uses PostgreSQL. ARGUS owns schema `argus` and currently stores collections, collection idempotency mappings, worker/lease state, observations, evidence, temporal snapshots and SiteRecipe state there.

Migrations:

```bash
python -m argus.storage.cli migrate
python -m argus.storage.cli check
```

Migrations are versioned and checksummed. Application startup refuses to become ready when the PostgreSQL schema is absent or at the wrong version.

### Local development

SQLite remains available for isolated local development and tests:

```bash
ARGUS_EXECUTION_ROLE=embedded
ARGUS_STORAGE_BACKEND=sqlite
ARGUS_DB_PATH=.argus/argus.sqlite3
```

For direct PostgreSQL development:

```bash
ARGUS_STORAGE_BACKEND=postgresql
ARGUS_DATABASE_DSN=postgresql://user:password@127.0.0.1:5432/argus
```

ARGUS also accepts `ARGUS_DATABASE_DSN_FILE`. When the Geo Analyzer aliases are present, the secret-file form is preferred over the environment-string form.

## Local install

Python 3.11+ is required.

```bash
python -m venv .venv
. .venv/bin/activate              # Windows: .venv\\Scripts\\activate
pip install -e '.[dev]'
playwright install chromium
```

Create a local token once:

```bash
argus init-token
```

Run:

```bash
argus serve
```

The default local bind is `127.0.0.1:8787`. Local CLI/API use `embedded` execution unless configured otherwise.

## API

Module-management endpoints:

- `GET /v1/manifest` — authenticated runtime identity/capabilities;
- `GET /v1/health` — service/database/worker readiness;
- `HEAD /v1/health` — readiness status code.

Collection API:

- `POST /v1/collections`;
- `GET /v1/collections/{collection_id}`;
- `GET /v1/collections/{collection_id}/result`;
- `POST /v1/collections/{collection_id}/cancel`.

Capabilities/operations/sources:

- `GET /v1/capabilities`;
- `GET /v1/operations/queue`;
- `GET /v1/sources`;
- `GET /v1/sources/{source_id}/health`.

All endpoints except `GET/HEAD /v1/health` require `Authorization: Bearer <token>`.

`GET /v1/capabilities` exposes the active storage/execution mode. Server API reports `queue_backend=postgresql_leases`, `idempotent_submission=true`, the idempotency/retention configuration and `worker_required_for_readiness=true`. Local embedded mode reports `queue_backend=embedded`.

`consumer` records who requested the data; it does not select a Kraken/Janus branch. `intents` define factual research goals.

## CLI

```bash
argus collect --consumer test --address "Ижевск, Пушкинская, 277" --intent public_mentions
argus status <collection_id>
argus result <collection_id>
argus sources
```

## Discovery

### SearXNG

A configured/self-hosted SearXNG JSON endpoint is the preferred external discovery provider:

```bash
ARGUS_SEARXNG_URL=http://127.0.0.1:8888
ARGUS_DISCOVERY_MAX_QUERIES=8
ARGUS_SEARXNG_MAX_RESULTS_PER_QUERY=10
```

ARGUS accesses it as a separate HTTP service and does not vendor or import SearXNG code.

### DuckDuckGo browser fallback

When enabled, Playwright submits the public DuckDuckGo no-JS HTML form. This is a low-volume fallback, not a private API integration.

```bash
ARGUS_BROWSER_SERP_ENABLED=true
ARGUS_BROWSER_SERP_MAX_RESULTS_PER_QUERY=5
ARGUS_BROWSER_SERP_WAIT_MS=750
```

CAPTCHA/access challenges are reported as blocked; ARGUS does not attempt bypasses.

### Intent-specific planning

Discovery is run independently for uncovered intents while sharing one collection-level query budget. A seed URL does not automatically mark every intent covered. If the same URL is discovered for several intents, ARGUS fetches it once and merges the research goals into provenance.

Discovery progress is checkpointed after each intent. A process/worker restart therefore resumes only unfinished discovery branches.

## Same-host robots.txt and Sitemap

After a top-level HTML fetch, ARGUS can inspect same-host `robots.txt` and `/sitemap.xml` for bounded navigation candidates.

```bash
ARGUS_SITEMAP_DISCOVERY_ENABLED=true
ARGUS_SITEMAP_MAX_URLS=20
ARGUS_SITEMAP_MAX_INDEXES=5
```

Only same-host HTTP(S) candidates are accepted; domain constraints still apply. Sitemap tasks consume the normal page budget. Sitemap discovery is navigation-only and fail-open when missing, malformed or unavailable.

RSS/Atom and Sitemap XML use `defusedxml` to reject unsafe DTD/entity payloads.

## JSON-LD

ARGUS extracts bounded `application/ld+json` entities embedded in HTML. It does not dereference remote contexts or perform hidden network requests. Accepted entities receive separate `source_kind=json_ld` Observation/Evidence records backed by the same page snapshot.

## Historical research and Wayback

`historical_context` can trigger bounded recursive discovery from already collected factual labels. Follow-up labels remain navigation hypotheses until a new source is fetched.

Optional Wayback CDX support performs exact-URL capture lookup only:

```bash
ARGUS_WAYBACK_CDX_URL=https://web.archive.org/cdx/search/cdx
ARGUS_WAYBACK_CAPTURE_BASE_URL=https://web.archive.org/web
ARGUS_WAYBACK_MAX_CAPTURES=5
```

A CDX row proves a capture exists. Archived page content is fetched separately through the normal Generic Web pipeline before becoming page Evidence.

## Optional OpenStreetMap providers

No public Overpass or Nominatim service is enabled silently.

```bash
ARGUS_OVERPASS_URL=https://overpass.example/api/interpreter
ARGUS_NOMINATIM_URL=https://nominatim.example
```

OpenStreetMap facts preserve `© OpenStreetMap contributors` / ODbL provenance and public `openstreetmap.org` evidence URLs.

## Retry and rate behavior

Crawlee owns normal crawler concurrency/session/retry behavior. Direct providers use their own minimum-interval gates and bounded 429/503 retries.

A valid `Retry-After` is authoritative. ARGUS never shortens a server-requested wait to make an earlier retry. If the delay exceeds the configured maximum acceptable wait, the current operation ends without an early second request.

## Security boundary

ARGUS includes application-level defenses for:

- HTTP(S)-only arbitrary targets;
- userinfo rejection;
- resolved private/loopback/link-local/reserved/multicast/cloud-metadata target blocking unless explicitly allowlisted;
- redirect-hop validation in FAST;
- unsafe browser request blocking;
- response/browser time and size limits;
- hardened XML/JSON-LD parsing;
- Bearer token files outside Git;
- PostgreSQL secret-file preference and `SecretStr` handling;
- stale-worker protection for terminal cancellation;
- bounded queue admission and retry semantics;
- structured log/error redaction;
- CAPTCHA/access-control non-bypass.

Application SSRF checks are defense in depth. Server deployment must also enforce network-level egress policy.

## Development rule

ARGUS remains factual infrastructure. Competition scoring, demand interpretation, risk models and other analytical conclusions belong to Kraken, Janus or other consumers. New providers must preserve the common SourceAdapter/Repository/provenance contracts and must not introduce branches keyed by consumer identity.

See `docs/ARCHITECTURE.md` for the detailed design and `geo-analyzer-module.json` for the server deployment contract.
