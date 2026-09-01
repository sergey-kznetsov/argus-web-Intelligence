# ARGUS architecture

## Boundary

ARGUS `0.3.0` is a standalone server-side infrastructure service for analytical consumers such as Kraken, Janus and future Geo Analyzer modules.

It is **not** an installable Geo Analyzer module and must not appear in the user analysis UI or in Module Manager lifecycle.

```text
User
  -> Geo Analyzer
  -> selected analytical module
  -> ARGUS
  -> public web / open sources
  -> Observation + Evidence + Provenance + Coverage
  -> analytical module
  -> Module Result
  -> Geo Analyzer report / UI
```

Responsibility boundary:

```text
Geo Analyzer = orchestrate + present
ARGUS        = find + obtain + prove + store + continue researching
Module       = normalize + interpret + calculate + conclude
```

ARGUS never branches on consumer identity. `consumer` is identity/provenance/idempotency metadata; research behavior is described by territory, requested facts, intents and bounded constraints.

## Standalone server deployment

ARGUS lifecycle belongs to the server deployment, not to Geo Analyzer.

Windows server layout:

```text
C:\argus\releases\<immutable-commit>        application release
C:\ProgramData\ARGUS\argus.env             runtime configuration
C:\ProgramData\ARGUS\secrets\argus.token  bearer token
C:\ProgramData\ARGUS\secrets\database-dsn.txt
C:\ProgramData\ARGUS\logs                  service logs
ARGUS-API                                    Scheduled Task
ARGUS-Worker                                 Scheduled Task
```

Default internal endpoints:

```text
API          http://127.0.0.1:8787
worker probe http://127.0.0.1:8788/readyz
```

The deployment contract is `deploy/windows/deploy-server.ps1`.

Deployment uses an immutable 40-character Git commit SHA. It:

1. downloads the pinned repository snapshot;
2. creates an isolated Python virtual environment in the release;
3. installs ARGUS and Chromium;
4. creates/preserves the server-owned bearer token;
5. reads the shared physical PostgreSQL connection from the server configuration and exposes it to ARGUS through a protected secret file;
6. runs ARGUS migrations and schema verification before cutover;
7. registers `ARGUS-Worker` and `ARGUS-API` as SYSTEM Scheduled Tasks;
8. waits for worker readiness and API readiness;
9. records the active immutable release;
10. rolls the tasks back to the previous release if the new release fails health-check.

The shared PostgreSQL instance does not make ARGUS part of Geo Analyzer storage. ARGUS owns only PostgreSQL schema `argus`; migrations and operational procedures are schema-scoped.

Server roles are explicit:

```text
API process     ARGUS_EXECUTION_ROLE=api
Worker process  ARGUS_EXECUTION_ROLE=worker
Local/dev       ARGUS_EXECUTION_ROLE=embedded
```

PostgreSQL is mandatory for server `api` and `worker`. SQLite is reserved for embedded/local development and isolated fixtures.

## Consumer connection contract

Geo Analyzer TEST, Geo Analyzer PROD and installed analytical modules use the same server-level connector contract:

```text
ARGUS_SERVICE_BASE_URL=http://127.0.0.1:8787
ARGUS_SERVICE_TOKEN_FILE=C:\ProgramData\ARGUS\secrets\argus.token
```

Geo Analyzer does not implement ARGUS-specific lifecycle logic. Its generic service environment is inherited by module processes. A module that requires public-web intelligence owns its `ArgusConnector` and decides when to call ARGUS.

A module must not hardcode the endpoint, port or bearer token.

## Collection API

The main authenticated consumer API is:

```text
POST /v1/collections
GET  /v1/collections/{collection_id}
GET  /v1/collections/{collection_id}/result
GET  /v1/collections/{collection_id}/result/summary
GET  /v1/collections/{collection_id}/result/observations
GET  /v1/collections/{collection_id}/result/evidence
POST /v1/collections/{collection_id}/cancel
```

Runtime/service endpoints:

```text
GET  /v1/health
HEAD /v1/health
GET  /v1/manifest              Bearer required
GET  /v1/capabilities          Bearer required
GET  /v1/operations/queue      Bearer required
GET  /v1/operations/collections Bearer required
```

Large results are delivered through bounded summary and opaque-keyset paginated Observation/Evidence endpoints rather than unbounded response bodies.

## Server queue and recovery

The API process never executes server collections itself. It persists work and returns an accepted collection ID. Workers claim queued work from PostgreSQL.

```text
Module
  -> POST /v1/collections
  -> argus.collections(status=queued)
  -> worker claim FOR UPDATE ... SKIP LOCKED
  -> argus.collection_leases
  -> CollectionOrchestrator.execute()
  -> Observation + Evidence + Snapshots
```

`argus.worker_instances` stores worker heartbeats. `argus.collection_leases` stores exclusive collection ownership.

Important guarantees:

- only queued/running collections can be claimed;
- active leases exclude other workers;
- expired work can be recovered by another worker;
- SQL lease fencing rejects stale-worker writes after ownership transfer;
- persistent checkpoints prevent blind full restarts;
- deterministic Observation/Evidence/Snapshot identities make replay converge;
- terminal cancellation cannot be overwritten by stale workers.

## Readiness

`GET /v1/health` is readiness, not a static liveness string.

For server API role it requires:

1. PostgreSQL reachable;
2. ARGUS schema at the expected migration version;
3. at least one recent worker heartbeat.

If the worker is unavailable, API readiness is degraded and the standalone deployment must not treat cutover as successful.

The worker exposes a loopback `/readyz` probe.

## Queue admission and idempotency

Server submission is idempotent at the PostgreSQL boundary.

`CollectionRequest` may contain `idempotency_key`. ARGUS also computes a canonical SHA-256 fingerprint of the factual request.

Within the configured idempotency window:

- same key + same request returns the existing collection;
- same key + different request returns conflict;
- omitted key uses canonical request identity;
- new analyses naturally separate through `analysis_id`.

Queue admission is bounded globally and per consumer. Existing idempotent retries are returned even if the queue later becomes full; new work can receive controlled `429`/`503` with `Retry-After`.

## Main research pipeline

```text
Collection Orchestrator
  -> Research Planner
  -> discovery queries
  -> DiscoveryService
  -> destination candidates
  -> SourceRegistry
  -> FAST
  -> BROWSER when FAST is insufficient
  -> AGENT only when deterministic navigation is insufficient
  -> verified SiteRecipe for reusable learned navigation
  -> Observation + Evidence + Provenance
  -> PostgreSQL
  -> recursive branches / coverage-gap check
  -> stop at bounded budget or when no meaningful branch remains
```

Search snippets, Sitemap entries and discovery metadata are navigation candidates, not facts. A normal factual source must be fetched before it can become Evidence.

AGENT output is never factual authority. Learned navigation must be converted into a candidate SiteRecipe and verified by deterministic browser replay before reuse.

CAPTCHA and access controls are not bypassed. They produce blocked/partial coverage and research continues through other permitted public sources.

## Discovery and sources

ARGUS supports common source contracts instead of consumer-specific parsers. Source adapters implement factual acquisition and normalization; they do not contain Kraken/Janus/Historical business logic.

Current families include generic web, RSS/Atom, Sitemap navigation, embedded JSON-LD, Wayback CDX, OpenStreetMap/Overpass and optional Nominatim, with extension points for public portals, documents, maps, archives, reviews and discussions.

Discovery providers are ordered fallbacks. Optional configured SearXNG can be used first; browser-based public discovery can be used as a low-volume fallback. Discovery is bounded by request budgets and persistent deduplication.

## Evidence and provenance

Every factual Observation must be traceable to Evidence/provenance. LLM prose and search snippets are not Evidence.

The factual layer preserves, where applicable:

- source and source kind;
- source URL;
- normalized entity identity;
- extracted factual text/data;
- geographic evidence;
- publication time when source-backed;
- collection time separately;
- content hash;
- extraction/provenance metadata;
- quality/confidence and limitations.

Unknown publication time remains `null`; collection time must not be substituted for publication time.

## Recursive and historical research

Research can branch from entities found in already fetched factual material. Branches remain hypotheses until separately fetched Evidence confirms them.

Recursive work is bounded by depth, page count, query budget, deduplication and persistent checkpoints.

Historical capability combines ARGUS snapshots with public archive/navigation sources and can produce evidence-backed dated observations without fabricating a single narrative when sources conflict.

## Storage

ARGUS owns PostgreSQL schema `argus`.

Major server relations include:

- `collections`;
- `collection_idempotency`;
- `collection_leases`;
- `worker_instances`;
- `observations`;
- `evidence`;
- `snapshots`;
- `site_recipes`;
- schema migration and result-access metadata.

Migrations are versioned/checksummed and protected by a PostgreSQL advisory lock. Backup/restore and retention are schema-scoped and must not affect Geo Analyzer schemas.

## Free base contour

ARGUS base operation must not require paid search, paid proxy networks, commercial CAPTCHA solving, paid browser clouds, mandatory paid Google/Yandex/2GIS APIs or mandatory paid LLMs.

Allowed base mechanisms include public HTML, open endpoints, RSS/Atom, public files/data, open-source runtimes and self-hosted/free services.

## Security

Server boundaries include:

- loopback-only API and worker probe by default;
- Bearer token in protected secret file;
- PostgreSQL DSN in protected secret file;
- URL/redirect/SSRF validation;
- request/response/download/browser resource limits;
- bounded retries and recursion;
- safe XML/JSON handling;
- secret-safe structured logging;
- lease fencing and cancellation consistency;
- no CAPTCHA/access-control bypass.

Application SSRF controls are defense in depth; production network egress policy remains an infrastructure responsibility.

## Definition of done

A capability is complete only when:

1. the real service graph produces the intended result;
2. factual output has Evidence/provenance;
3. failure/recovery behavior is defined;
4. budgets/resource limits are explicit;
5. standalone verification can demonstrate the path where applicable;
6. regression/integration tests cover the important behavior;
7. documentation matches runtime architecture;
8. CI is green.

ARGUS must remain a replaceable server infrastructure service. Connecting a new analytical module must require only the common Collection API and deployment-owned `ARGUS_SERVICE_*` connector, not a change to ARGUS or Geo Analyzer Core keyed by the new module name.
