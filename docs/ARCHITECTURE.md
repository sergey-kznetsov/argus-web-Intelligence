# ARGUS architecture

## Boundary

ARGUS is an infrastructure backend, not an analytical module. Consumers provide territory, intents and constraints. ARGUS selects discovery providers, source adapters and retrieval runtimes without branches keyed by consumer identity.

```text
Consumer -> Internal API -> Collection Orchestrator -> Research Planner
                                      |                 |
                                      |                 v
                                      |            search queries
                                      |                 |
                                      v                 v
                                SourceRegistry    DiscoveryService
                                      |           SearXNG -> browser fallback
                                      |                 |
                                      +<--- destination URLs
                                      |
                         FAST -> BROWSER -> AGENT
                                      |
                         Observation + Evidence
                                      |
                    SQLite Repository + Snapshots

Map intents -> OverpassSourceAdapter -> optional Nominatim geocode
                                      -> Overpass provider
                                      -> Observation + Evidence + Snapshot
```

Discovery results are navigation candidates, not facts. Search snippets never become Evidence. ARGUS fetches the destination page through a factual source adapter before creating Observation/Evidence.

## Runtime escalation

FAST uses a persistent Crawlee HTTP crawler for static HTML/XML/JSON/public endpoints. BROWSER uses a persistent Crawlee Playwright crawler for JavaScript interaction. AGENT is optional and only used when deterministic retrieval/recipes cannot solve a public site.

The service keeps Crawlee request/session/retry/concurrency machinery instead of reimplementing it. FAST can use Crawlee `ThrottlingRequestManager` for explicitly configured domains. Persistent FAST/BROWSER runtimes are shut down by the FastAPI service lifecycle.

BROWSER tracks the latest main-document response during SiteRecipe navigation. If a recipe starts on HTTP 200 and navigates to a 403/429/etc. page, the final status and content type are returned rather than the original navigation metadata.

## Discovery

`ResearchPlanner` produces queries. `DiscoveryService` runs ordered providers as fallbacks and stops after the first provider that produces valid destination URLs.

Current providers:

1. optional self-hosted SearXNG JSON API;
2. low-volume DuckDuckGo HTML Playwright fallback when enabled.

The browser fallback submits the public no-JS search form in a real browser. CAPTCHA/anti-bot/access challenges are never bypassed. A fully blocked discovery with no destination URL is reported as collection `blocked`.

If all configured discovery providers complete normally but return no valid destination URL, ARGUS records `DISCOVERY_NO_RESULTS`. This prevents a mixed request from being reported as fully complete when another source covered only some intents. Existing factual data plus an uncovered intent becomes `partial`; a request with no executable source remains `failed`.

Discovery is only run for intents not already covered by self-discovering source adapters or explicit seed tasks. The checkpoint stores `planning_complete` so restart recovery does not rerun discovery after planning has already completed.

## Research Planner

`OllamaResearchPlanner` uses the local Ollama HTTP API and falls back to deterministic planning if Ollama is unavailable. Planning may create research queries but never facts. Factual output must originate from an Observation/Evidence pair.

Recursive crawling is bounded by collection `max_pages` and `max_depth`. Discovered tasks are persisted in the collection checkpoint before the next page is processed, so a restart can resume unfinished branches. Deterministic Observation/Evidence identities prevent duplicates if data was stored immediately before a crash but the checkpoint was not yet updated.

## SiteRecipe

A recipe is versioned by domain + goal and stores deterministic browser steps. Recipes can wait for fixed durations or bounded Playwright load states.

When deterministic browsing fails and an enabled AGENT finds a path, ARGUS compiles only supported/reproducible actions into a candidate recipe. The candidate must succeed in a Playwright replay before it is persisted. Broken recipes are marked failed and can be replaced by a newer validated version.

## History

Every successful factual collection creates temporal snapshots containing `collected_at`, SHA-256 `content_hash`, `source_url`, `source_id`, `extractor_version` and normalized/raw content. Unchanged facts still create a temporal snapshot with `diff=null`; changed facts also store a unified diff against the previous snapshot.

Web documents snapshot fetched content. Map places snapshot canonical normalized POI facts so changes to coordinates, address, categories or attributes can be detected over time.

## Map and geocoding providers

`argus.maps` defines provider-neutral contracts: `MapSearchRequest`, `MapPlace`, `MapSearchResult`, provider capabilities and `MapProviderRegistry`.

The first real map provider is optional `openstreetmap_overpass`. It is registered only when `ARGUS_OVERPASS_URL` is configured. The corresponding `OverpassSourceAdapter` is a normal factual source adapter and is activated by neutral POI intents such as `school`, `kindergarten`, `hospital`, `pharmacy`, `cafe`, `supermarket` and `park`. It never branches on `consumer`.

Overpass itself requires coordinates. If a collection already contains `territory.point`, no geocoder is called. For address/city-only requests, the adapter can use an optional `GeocodeProvider`. The current implementation is Nominatim, enabled only by an explicit `ARGUS_NOMINATIM_URL`. The selected geocoding candidate is retained in Observation/Evidence provenance.

No public Overpass or Nominatim endpoint is silently enabled by default. Operators must configure a self-hosted or explicitly approved endpoint. This keeps rate-policy choices at deployment level and avoids treating donated public infrastructure as an unlimited backend.

Map/geocoding contracts contain collection facts only. Competition scoring, demand interpretation, risk assessment and other consumer analytics remain outside ARGUS.

## Storage

The orchestrator depends on `Repository`, not `SQLiteRepository`. PostgreSQL can implement the same contract later. SQLite is WAL-enabled for standalone/dev use.

## Security

- localhost bind by default;
- Bearer token stored outside Git and created atomically;
- empty/invalid token files are not accepted as credentials;
- arbitrary URLs limited to HTTP(S);
- URL userinfo rejected;
- DNS-resolved loopback/private/link-local/reserved/multicast/metadata targets rejected unless explicitly allowlisted;
- FAST validates every redirect hop before HTTPX sends it;
- BROWSER intercepts and validates page network requests and unsafe redirects;
- response/browser size, time and concurrency/rate limits;
- CAPTCHA/access blocks surfaced as blocked/partial, never bypassed;
- structured errors and JSON logs redact common credential forms and URL query strings.

Production still requires network egress controls because application URL validation is defense in depth and cannot eliminate all DNS rebinding/transport-layer risks.

## Deferred provider implementations

2GIS, Yandex Maps, Google Maps, GIS ЖКХ and developer/public-portal adapters remain separate provider implementations. They must use the common contracts, preserve source provenance and remain free of consumer analytics.
