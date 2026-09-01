# ARGUS Web Intelligence

ARGUS `0.3.0` is a server-side evidence-first web intelligence backend for Kraken, Janus and future analytical consumers in the Geo Analyzer ecosystem.

Core rule: **ARGUS = find + obtain + prove + store. Consumers = interpret + calculate + conclude.**

ARGUS is one standalone server-level infrastructure service. Geo Analyzer does not install, update, stop or delete it. TEST, PROD and analytical consumers call the same localhost ARGUS API through a server-owned endpoint and token-file contract.

## Current product architecture

ARGUS currently provides:

- protocol `1.0.0` CollectionRequest/CollectionResult contracts;
- separate server API and collection-worker processes;
- PostgreSQL-backed queue with worker heartbeat and per-collection leases;
- atomic queue claim through `FOR UPDATE ... SKIP LOCKED`;
- FIFO recovery so interrupted older work is not starved by new submissions;
- SQL lease fencing so expired/transferred workers cannot mutate collection output;
- replay-safe worker recovery after process or PostgreSQL interruption;
- idempotent server collection submission with a bounded 24-hour default retry window;
- atomic queue admission/backpressure across concurrent API processes;
- authenticated operational queue metrics and keyset-paginated collection listing;
- bounded automatic PostgreSQL retention under a shared maintenance lock;
- bounded result delivery with summary + opaque keyset pages for large collections;
- terminal PostgreSQL cancellation that stale workers cannot overwrite;
- persistent research/discovery checkpoints for restart recovery;
- product PostgreSQL storage in dedicated schema `argus`;
- local/dev SQLite backend through the same Repository contract;
- versioned/checksummed PostgreSQL migrations protected by an advisory lock;
- Psycopg 3 native async pools with explicit startup/shutdown lifecycle;
- persistent Crawlee FAST and Playwright BROWSER runtimes;
- optional local AGENT escalation through Browser Use/Ollama;
- deterministic SiteRecipe replay before recipe persistence;
- SearXNG discovery with DuckDuckGo HTML browser fallback;
- Generic Web, RSS/Atom, Sitemap, Wayback CDX and OpenStreetMap/Overpass factual paths;
- optional Nominatim geocoding;
- bounded recursive historical research;
- embedded bounded JSON-LD extraction without remote `@context` dereferencing;
- Observation + Evidence + provenance + SHA-256 temporal snapshots/diffs;
- deterministic task/Observation/Evidence and collection-scoped Snapshot identities;
- per-source operational health;
- redirect-aware SSRF defenses, Bearer authentication, inbound-body/resource/rate limits and secret-safe logging;
- GitHub CI configured with a PostgreSQL service.

Discovery results and navigation hints are not facts. Search snippets, Sitemap entries and archive navigation metadata only seed factual retrieval. A destination page must be fetched before it can become page Evidence. Embedded JSON-LD is evidence only because it is contained in the already fetched page.

## Standalone server deployment

ARGUS is deployed independently of Geo Analyzer under `C:\\argus` with configuration and secrets under `C:\\ProgramData\\ARGUS`. On Windows Server the service is composed of `ARGUS-API` and `ARGUS-Worker` SYSTEM scheduled tasks, both loopback-only.

Geo Analyzer consumers receive only these generic server variables:

```text
ARGUS_SERVICE_BASE_URL=http://127.0.0.1:8787
ARGUS_SERVICE_TOKEN_FILE=C:\\ProgramData\\ARGUS\\secrets\\argus.token
```

The standalone deploy performs migrations before cutover, waits for API/worker readiness and rolls back the scheduled tasks to the previous immutable release if health fails. See [`docs/SERVER_DEPLOYMENT_WINDOWS.md`](docs/SERVER_DEPLOYMENT_WINDOWS.md).

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

Worker execution is additionally fenced at the PostgreSQL mutation boundary. Collection state, Observation, Evidence and collection-scoped Snapshot writes are accepted only while the current worker still owns a non-expired lease. If another worker already claimed an expired lease, the old worker cannot commit a late checkpoint or factual row even before its next heartbeat detects the transfer.

Lease-owned PostgreSQL failures abort the current attempt with replay semantics rather than being recorded as `SOURCE_ERROR`. An unfinished task therefore remains absent from the durable `visited` set and can be retried from the last checkpoint. Observation/Evidence IDs and unchanged collection-scoped Snapshot IDs are deterministic, so replay converges on the already persisted rows after a crash between factual persistence and checkpoint persistence.

Claim ordering is FIFO by collection creation time regardless of whether a record is freshly `queued` or recovered `running`. This prevents interrupted older work from being permanently displaced by a continuous stream of new requests.

Defaults:

```bash
ARGUS_WORKER_CONCURRENCY=2
ARGUS_WORKER_POLL_INTERVAL_SECONDS=1
ARGUS_WORKER_LEASE_SECONDS=90
ARGUS_WORKER_HEARTBEAT_SECONDS=20
ARGUS_WORKER_HEALTH_MAX_AGE_SECONDS=60
```

`ARGUS_WORKER_HEARTBEAT_SECONDS` must be shorter than the lease duration.

Cancellation is terminal in PostgreSQL. If API records `cancelled`, a stale worker cannot change the collection back to `running`; Observation/Evidence writes that start after the cancellation is visible are rejected by the storage layer. A network operation already in flight may finish before the worker observes cancellation, but it cannot resurrect the collection state.

Detailed failure/replay contract: `docs/RECOVERY.md`.

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
- an explicit key is scoped by `consumer`;
- reusing one consumer's key for a different request inside the window returns HTTP `409`;
- blank keys are rejected;
- without a key ARGUS uses a SHA-256 fingerprint of the canonical request;
- the transport `idempotency_key` is excluded from that fingerprint;
- default window is 86,400 seconds (24 hours);
- after expiry the same request/key may intentionally create a fresh collection.

```bash
ARGUS_IDEMPOTENCY_WINDOW_SECONDS=86400
```

Because `analysis_id` is part of the request fingerprint, a genuinely new consumer analysis should use a new analysis ID.

## Queue admission and operations

New server collections are admitted under a PostgreSQL transaction-level advisory lock, making count-and-insert atomic across API processes.

```bash
ARGUS_QUEUE_MAX_ACTIVE_COLLECTIONS=500
ARGUS_QUEUE_MAX_ACTIVE_PER_CONSUMER=100
ARGUS_QUEUE_RETRY_AFTER_SECONDS=15
```

An already-admitted idempotent retry bypasses capacity rejection. New requests above the per-consumer limit return `429`; global saturation returns `503`; both include `Retry-After`.

Authenticated operational endpoints:

- `GET /v1/operations/queue` — queued/running counts, leases, workers and oldest job ages;
- `GET /v1/operations/collections` — summary-only collection history with `status`/`consumer` filters and opaque keyset cursor, maximum 100 rows per page.

These endpoints read PostgreSQL directly and do not create a second operational datastore.

## Retention

Server workers run bounded retention passes automatically. A PostgreSQL advisory lock elects one maintainer at a time.

```bash
ARGUS_RETENTION_MAINTENANCE_INTERVAL_SECONDS=3600
ARGUS_RETENTION_COLLECTION_DAYS=180
ARGUS_RETENTION_SNAPSHOT_DAYS=365
ARGUS_RETENTION_WORKER_REGISTRATION_DAYS=7
ARGUS_RETENTION_BATCH_SIZE=500
```

Rules:

- active `queued`/`running` collections are never purged;
- only terminal collections older than collection retention are removed;
- child Observation/Evidence/idempotency/lease rows follow foreign-key cleanup;
- expired idempotency mappings are independently removed;
- stale worker registrations are removed after their retention period;
- old snapshots are removed in bounded batches, but the newest snapshot for each `source_url` is preserved;
- SiteRecipe records are not automatically purged;
- snapshot retention cannot be shorter than collection retention.

Manual operator commands are also available:

```bash
python -m argus.storage.cli operations
python -m argus.storage.cli retention
```

## Bounded result delivery

The legacy `GET /v1/collections/{collection_id}/result` remains compatible for small collections, but ARGUS checks storage size before loading rows into API memory.

Defaults:

```bash
ARGUS_API_FULL_RESULT_MAX_ITEMS=100
ARGUS_API_FULL_RESULT_MAX_BYTES=4194304
ARGUS_API_RESULT_PAGE_DEFAULT_SIZE=50
ARGUS_API_RESULT_PAGE_MAX_SIZE=100
ARGUS_API_RESULT_PAGE_MAX_BYTES=2097152
```

When either full-result limit is exceeded, `/result` returns HTTP `409` with `detail.code=RESULT_REQUIRES_PAGINATION`. ARGUS never silently truncates a successful `CollectionResult`.

Consumers then use:

- `GET /v1/collections/{collection_id}/result/summary`;
- `GET /v1/collections/{collection_id}/result/observations`;
- `GET /v1/collections/{collection_id}/result/evidence`.

Observation/Evidence pages use opaque cursors bound to both the collection and result kind. A cursor cannot be reused for another collection or swapped between observation/evidence streams. Pages are bounded by both item count and stored JSON bytes and report `page_stored_bytes`.

Paged traversal is available only after a terminal collection state. A `queued` or `running` collection returns `409 RESULT_NOT_FINAL`. PostgreSQL result reads use a separate small async pool and `REPEATABLE READ READ ONLY` transactions so a retention pass cannot produce a mixed response midway through one read.

Detailed contract: `docs/RESULT_DELIVERY.md`.

## Storage

### Server/product

Server deployment uses PostgreSQL. ARGUS owns schema `argus` and stores collections, idempotency mappings, worker/lease state, observations, evidence, temporal snapshots and SiteRecipe state there. Product runtime uses `FencedPostgresRepository`, which keeps normal API/admin behavior while enforcing lease ownership for worker execution.

```bash
python -m argus.storage.cli migrate
python -m argus.storage.cli check
```

Migrations are versioned and checksummed. Application startup refuses readiness if the schema is missing or at the wrong version.

### Local development

SQLite implements the same core collection storage contract for local/embedded development. Snapshot inserts are idempotent by `snapshot_id` so local crash-replay fixtures preserve the same identity behavior as server recovery.

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

ARGUS also accepts `ARGUS_DATABASE_DSN_FILE`; the secret-file form is preferred when available.

## Local install

Python 3.11+ is required.

```bash
python -m venv .venv
. .venv/bin/activate              # Windows: .venv\\Scripts\\activate
pip install -e '.[dev]'
playwright install chromium
argus init-token
argus serve
```

The default local bind is `127.0.0.1:8787` and local CLI/API use embedded execution unless configured otherwise.

## API

Module management:

- `GET /v1/manifest`;
- `GET /v1/health`;
- `HEAD /v1/health`.

Collections/results:

- `POST /v1/collections`;
- `GET /v1/collections/{collection_id}`;
- `GET /v1/collections/{collection_id}/result`;
- `GET /v1/collections/{collection_id}/result/summary`;
- `GET /v1/collections/{collection_id}/result/observations`;
- `GET /v1/collections/{collection_id}/result/evidence`;
- `POST /v1/collections/{collection_id}/cancel`.

Capabilities/operations/sources:

- `GET /v1/capabilities`;
- `GET /v1/operations/queue`;
- `GET /v1/operations/collections`;
- `GET /v1/sources`;
- `GET /v1/sources/{source_id}/health`.

All endpoints except `GET/HEAD /v1/health` require `Authorization: Bearer <token>`. Inbound request bodies are limited by `ARGUS_API_MAX_REQUEST_BYTES` (1 MiB by default), including streamed/chunked bodies.

`consumer` records who requested data; it never selects a Kraken/Janus branch. `intents` define factual research goals.

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

ARGUS accesses it as a separate HTTP service and does not vendor/import SearXNG code.

### DuckDuckGo browser fallback

When enabled, Playwright submits the public DuckDuckGo no-JS HTML form. This is a low-volume fallback, not a private API integration.

```bash
ARGUS_BROWSER_SERP_ENABLED=true
ARGUS_BROWSER_SERP_MAX_RESULTS_PER_QUERY=5
ARGUS_BROWSER_SERP_WAIT_MS=750
```

CAPTCHA/access challenges are reported as blocked; ARGUS does not bypass them.

### Intent-specific planning

Discovery is run independently for uncovered intents while sharing one collection-level query budget. A seed URL does not automatically mark every intent covered. If one URL serves several intents, ARGUS fetches it once and merges research goals into provenance.

Discovery progress is checkpointed after each intent, so restart resumes only unfinished discovery branches.

## Same-host robots.txt and Sitemap

After a top-level HTML fetch, ARGUS can inspect same-host `robots.txt` and `/sitemap.xml` for bounded navigation candidates.

```bash
ARGUS_SITEMAP_DISCOVERY_ENABLED=true
ARGUS_SITEMAP_MAX_URLS=20
ARGUS_SITEMAP_MAX_INDEXES=5
```

Only same-host HTTP(S) candidates are accepted; domain constraints remain active. Sitemap tasks consume normal page budget and are navigation-only/fail-open.

RSS/Atom and Sitemap XML use `defusedxml` to reject unsafe DTD/entity payloads.

## JSON-LD

ARGUS extracts bounded `application/ld+json` entities embedded in HTML. It does not dereference remote contexts or perform hidden network requests. Accepted entities receive separate `source_kind=json_ld` Observation/Evidence backed by the fetched page snapshot.

## Historical research and Wayback

`historical_context` can trigger bounded recursive discovery from already collected factual labels. Follow-up labels remain hypotheses until a new source is fetched.

Optional Wayback CDX support performs exact-URL capture lookup only:

```bash
ARGUS_WAYBACK_CDX_URL=https://web.archive.org/cdx/search/cdx
ARGUS_WAYBACK_CAPTURE_BASE_URL=https://web.archive.org/web
ARGUS_WAYBACK_MAX_CAPTURES=5
```

A CDX row proves a capture exists. Archived content is fetched separately through the normal Generic Web pipeline before becoming page Evidence.

## Optional OpenStreetMap providers

No public Overpass or Nominatim endpoint is enabled silently.

```bash
ARGUS_OVERPASS_URL=https://overpass.example/api/interpreter
ARGUS_NOMINATIM_URL=https://nominatim.example
```

OpenStreetMap facts preserve `© OpenStreetMap contributors` / ODbL provenance and public `openstreetmap.org` evidence URLs.

## Retry and rate behavior

Crawlee owns ordinary crawler queue/session/retry/concurrency behavior. Direct providers use separate minimum-interval gates and bounded 429/503 retries.

A valid server `Retry-After` is authoritative. ARGUS never shortens it to make an earlier retry.

## Security boundary

ARGUS includes application-level defenses for:

- HTTP(S)-only arbitrary targets;
- URL userinfo rejection;
- private/loopback/link-local/reserved/multicast/cloud-metadata target blocking unless explicitly allowlisted;
- FAST redirect-hop validation;
- unsafe browser request blocking;
- inbound API body limits plus response/browser time and size limits;
- hardened XML/JSON-LD parsing;
- Bearer token files outside Git;
- PostgreSQL secret-file preference and `SecretStr` handling;
- terminal cancellation plus SQL lease fencing against stale workers;
- replay-safe handling of lease-owned PostgreSQL failures;
- bounded queue admission and result delivery;
- structured log/error redaction;
- CAPTCHA/access-control non-bypass.

Application SSRF validation is defense in depth. Production deployment must also enforce network-level egress policy.

## Development rule

ARGUS remains factual infrastructure. Competition scoring, demand interpretation, risk models and other analytical conclusions belong to Kraken, Janus or other consumers. New providers must preserve common SourceAdapter/Repository/provenance contracts and must not introduce branches keyed by consumer identity.

See `docs/ARCHITECTURE.md`, `docs/RECOVERY.md`, `docs/RESULT_DELIVERY.md` and `geo-analyzer-module.json`.
