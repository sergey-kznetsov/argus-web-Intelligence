# Discovery quality and duplicate-content policy

ARGUS discovery is navigation, not evidence. Search providers, provider ranks, snippets, canonical URL cleanup, locality matches and navigation scores only decide which public destination should be fetched first. A destination becomes factual material only after ARGUS fetches it and produces Observation + Evidence.

## Query planning

Research planning is bounded by `discovery_max_queries`. The heuristic planner allocates its budget round-robin across requested intents so one intent cannot consume the whole query budget before later intents are represented. Queries are normalized, deduplicated and length-bounded. Ollama planner output is subject to the same query-count and query-length limits; invalid or empty output falls back to the deterministic heuristic planner.

## Canonical navigation identity

Ordinary HTTP(S) navigation tasks use `discovery-url-identity/1`. Canonicalization is deliberately conservative:

- fragments are removed;
- host names are normalized, including IDNA;
- default ports are removed;
- known tracking query parameters such as UTM identifiers are removed;
- remaining query parameters and path semantics are preserved.

The fetched URL is still recorded as the factual source URL. A source-declared canonical URL or ARGUS navigation identity never rewrites the URL from which Evidence was actually obtained.

## Discovery ranking

`discovery-ranking/1` orders valid destinations deterministically by:

1. explicit `allowed_domains` priority;
2. provider rank;
3. locality-token matches from the requested city/address;
4. HTTPS as a tie-break;
5. canonical URL as the final stable tie-break.

ARGUS also emits an explainable `discovery_navigation_score` from those components. This score is a crawl-order/navigation score only. It is not source reliability, factual confidence or Evidence quality.

The navigation metadata is copied into factual provenance after the destination is fetched with:

- `navigation_only=true`;
- `is_evidence=false`.

## Stop conditions and budgets

Discovery uses `first_provider_with_valid_destinations`: later providers are not called once an earlier provider yields valid public destinations. This limits search traffic and avoids unnecessary anti-bot load.

The complete set of tasks emitted by one discovery call is bounded by `CollectionRequest.constraints.max_pages`. Historical Wayback companion tasks share this same budget with live destination tasks; they do not double it. If only one slot remains, ARGUS prioritizes the live factual fetch and records that an archive companion was skipped by budget.

`DiscoveryOutcome` exposes counters and a stop reason such as:

- `no_queries`;
- `first_provider_with_valid_destinations`;
- `task_budget_reached`;
- `blocked_without_destinations`;
- `no_valid_destinations`;
- `providers_exhausted`.

These values are operational/navigation telemetry, not Evidence.

## Exact duplicate-content suppression

ARGUS performs collection-scoped exact content deduplication only after content has been fetched and normalized. The identity is `committed-content-hash/1`.

Production PostgreSQL schema migration 8 adds an expression index over collection, `content_hash` and `source_kind` so duplicate lookup does not scan all observations. Embedded SQLite uses the same Repository contract for development/testing.

Duplicate lookup uses committed storage rather than a process-local cache. Therefore a source task whose atomic commit fails cannot poison duplicate state; a replacement worker can replay it safely after restart.

The duplicate policy is intentionally conservative:

- only primary document representations participate: `web_page`, `pdf_document`, `structured_data`, `office_document`, `office_spreadsheet`, `office_document_file`;
- embedded JSON-LD, Microdata, GeoJSON/KML child entities and other nested facts are not used to suppress an entire page;
- HTML `web_page` content must contain at least 256 normalized text characters before exact-hash suppression is eligible, reducing false suppression from small shared templates;
- the duplicate Observation and its Evidence are still stored;
- only newly discovered navigation tasks and historical branching from the duplicate are suppressed;
- provenance records the original committed Observation and URL through `duplicate_of` metadata.

ARGUS does not currently perform fuzzy, semantic or near-duplicate classification. It also does not deduplicate content across different collections. Those behaviours are intentionally excluded because they could merge genuinely distinct evidence without a sufficiently strong deterministic identity.
