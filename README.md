# ARGUS Web Intelligence

ARGUS is a server-side evidence-first web intelligence backend for Kraken, Janus and future analytical consumers in the Geo Analyzer ecosystem.

Core rule: **ARGUS = find + obtain + prove + store. Consumers = interpret + calculate + conclude.**

ARGUS is installed and supervised by the universal Geo Analyzer server-module manager, but it is intentionally hidden from the user analysis selection UI. Its runtime manifest sets `analysis_launch_toggle=false`; consumers call the ARGUS collection API directly.

## Current product architecture

ARGUS currently provides:

- protocol `1.0.0` CollectionRequest/CollectionResult contracts;
- asynchronous collection orchestration with persistent recovery checkpoints;
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
- GitHub CI configured with a real PostgreSQL service.

Discovery results and navigation hints are not facts. Search snippets, Sitemap entries and archive navigation metadata only seed factual retrieval. A destination page must be fetched before it can become page Evidence. Embedded JSON-LD is evidence only because it is contained in the already fetched page.

## Server deployment through Geo Analyzer

The repository root contains `geo-analyzer-module.json`. The universal manager can use it to install ARGUS from the GitHub repository without ARGUS-specific branches in Geo Analyzer.

The deployment contract requires:

- isolated Python virtual environment;
- Chromium installation for Playwright;
- shared PostgreSQL supplied through `GEOANALYZER_DATABASE_DSN` / `GEOANALYZER_DATABASE_DSN_FILE`;
- ARGUS migrations and schema check before process startup;
- a separate generated Bearer token file through `ARGUS_TOKEN_FILE`;
- localhost-only API process;
- authenticated `/v1/manifest` and `/v1/health` checks;
- automatic registration/enablement only after health succeeds.

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

## Storage

### Server/product

Server deployment uses PostgreSQL. ARGUS owns schema `argus` and stores collections, observations, evidence, temporal snapshots and SiteRecipe state there.

Migrations:

```bash
python -m argus.storage.cli migrate
python -m argus.storage.cli check
```

Migrations are versioned and checksummed. Application startup refuses to become ready when the PostgreSQL schema is absent or at the wrong version.

### Local development

SQLite remains available for isolated local development and tests:

```bash
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

The default local bind is `127.0.0.1:8787`.

## API

Module-management endpoints:

- `GET /v1/manifest` — authenticated runtime identity/capabilities;
- `GET /v1/health` — service/database readiness;
- `HEAD /v1/health` — readiness status code.

Collection API:

- `POST /v1/collections`;
- `GET /v1/collections/{collection_id}`;
- `GET /v1/collections/{collection_id}/result`;
- `POST /v1/collections/{collection_id}/cancel`.

Capabilities/sources:

- `GET /v1/capabilities`;
- `GET /v1/sources`;
- `GET /v1/sources/{source_id}/health`.

All endpoints except `GET/HEAD /v1/health` require `Authorization: Bearer <token>`.

The request contract is consumer-neutral:

```json
{
  "protocol_version": "1.0.0",
  "consumer": "kraken",
  "analysis_id": "analysis-id",
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

`consumer` records who requested the data; it does not select a Kraken/Janus branch. `intents` define the factual research goals.

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

Discovery progress is checkpointed after each intent. A process restart therefore resumes only unfinished discovery branches.

## Same-host robots.txt and Sitemap

After a top-level HTML fetch, ARGUS can inspect same-host `robots.txt` and `/sitemap.xml` for bounded navigation candidates.

```bash
ARGUS_SITEMAP_DISCOVERY_ENABLED=true
ARGUS_SITEMAP_MAX_URLS=20
ARGUS_SITEMAP_MAX_INDEXES=5
```

Only same-host HTTP(S) candidates are accepted; domain constraints still apply. Sitemap tasks consume the normal page budget. Sitemap discovery is navigation-only and is fail-open when missing, malformed or unavailable.

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
- structured log/error redaction;
- CAPTCHA/access-control non-bypass.

Application SSRF checks are defense in depth. Server deployment must also enforce network-level egress policy.

## Development rule

ARGUS remains factual infrastructure. Competition scoring, demand interpretation, risk models and other analytical conclusions belong to Kraken, Janus or other consumers. New providers must preserve the common SourceAdapter/Repository/provenance contracts and must not introduce branches keyed by consumer identity.

See `docs/ARCHITECTURE.md` for the detailed design and `geo-analyzer-module.json` for the server deployment contract.
