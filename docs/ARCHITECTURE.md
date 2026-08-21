# ARGUS architecture

## Boundary

ARGUS is an infrastructure backend, not an analytical module. Consumers provide territory, intents and constraints. ARGUS selects source adapters and retrieval runtimes without branches keyed by consumer identity.

```text
Consumer -> Internal API -> Collection Orchestrator -> Research Planner
                                      |                 |
                                      v                 v
                                SourceRegistry      research plan
                                      |
                         FAST -> BROWSER -> AGENT
                                      |
                         Observation + Evidence
                                      |
                    SQLite Repository + Snapshots
```

## Runtime escalation

FAST uses Crawlee HTTP crawling for static HTML/XML/JSON/public endpoints. BROWSER uses Crawlee Playwright for JavaScript interaction. AGENT is an optional backend boundary used only when deterministic retrieval/recipes cannot solve a site.

The orchestrator does not implement Crawlee's request queue, session pool or retry machinery again.

## Research Planner

`OllamaResearchPlanner` uses the local Ollama HTTP API and falls back to deterministic planning if Ollama is unavailable. Planning may create research queries but never facts. Factual output must originate from an Observation/Evidence pair.

Recursive crawling is bounded by collection `max_pages` and `max_depth`. Discovered tasks are persisted in the collection checkpoint before the next page is processed, so a restart can resume unfinished branches.

## SiteRecipe

A recipe is versioned by domain + goal and stores deterministic browser steps. Milestone 1 provides persistence and a Playwright executor. The automatic AGENT-success -> recipe synthesis -> validation loop is the next iteration; the persistence contract is already stable.

## History

Snapshots persist `collected_at`, SHA-256 `content_hash`, `source_url`, `source_id`, `extractor_version`, raw content and unified diff against the previous changed snapshot. Unchanged content does not create another stored snapshot.

## Storage

The orchestrator depends on `Repository`, not `SQLiteRepository`. PostgreSQL can implement the same contract later. SQLite is WAL-enabled for standalone/dev use.

## Security

- localhost bind by default;
- Bearer token stored outside Git;
- arbitrary URLs limited to HTTP(S);
- URL userinfo rejected;
- DNS-resolved loopback/private/link-local/reserved/multicast/metadata targets rejected unless explicitly allowlisted;
- redirect target revalidation;
- response/browser size and time limits;
- CAPTCHA/access blocks surfaced as blocked/partial, never bypassed;
- application errors are structured and do not include credentials.

Production still requires network egress controls because DNS can change between validation and connection (TOCTOU/DNS rebinding risk). Browser subresource interception is a future hardening item; the current milestone validates requested and final navigation URLs.

## Deferred adapters

SERP, 2GIS, Yandex Maps, Google Maps, GIS ЖКХ and developer-specific adapters are deliberately not part of milestone 1. They should implement `SourceAdapter` and remain free of consumer analytics.
