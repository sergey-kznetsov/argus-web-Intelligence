# ARGUS Web Intelligence — product charter

Status: binding product definition for the repository.

This document defines what ARGUS is, what it must do and what it must never become. A change that contradicts this charter is a product regression even if its unit tests pass.

## 1. Mission

ARGUS is a universal public-web intelligence collector for the Geo Analyzer ecosystem.

Its job is to receive a territory and research intents, independently discover relevant public sources, obtain the material, extract factual information, preserve evidence and provenance, expand the research from newly discovered entities, recover from failures, and return a complete structured factual corpus to the calling analytical module.

Core boundary:

```text
ARGUS = find + obtain + prove + store + continue researching
Analytical modules = interpret + calculate + conclude
```

ARGUS is not a report writer, risk scorer, parking model, marketing model or consumer-specific business engine.

## 2. Product, not an MVP

ARGUS is developed as a complete product. Milestones are implementation slices, not permission to remove the original requirements.

The following are not acceptable end states:

- a skeleton with placeholder interfaces;
- a set of unrelated parsers;
- a crawler that stops after the first search result page;
- a historical mode limited to comparing two HTML snapshots;
- a geo mode limited to returning POI names;
- an LLM used only to rewrite collected text;
- a source list without a working retrieval path;
- a feature declared complete only because one adapter exists.

A feature is complete only when its end-to-end product result works and is covered by automated tests or a reproducible standalone probe.

## 3. System position

```text
User
  ↓
Geo Analyzer
  ↓
selected analytical modules
  ↓
Kraken / Janus / Historical / future modules
  ↓
ARGUS
  ↓
public internet / sites / maps / archives / portals / documents
```

ARGUS is infrastructure. It must not appear as a user analysis checkbox or capability card. Geo Analyzer may install, start, health-check, update, reinstall and remove it.

ARGUS must never branch on consumer identity (`if kraken`, `if janus`, etc.). A consumer describes the requested information through `territory`, `intents` and constraints.

## 4. Input contract

A collection request describes:

- consumer;
- analysis id;
- city/address/coordinates/geometry;
- radius/territory context;
- one or more research intents;
- limits and optional domain constraints;
- seed URLs when known.

ARGUS decides which public sources and retrieval strategies are appropriate.

## 5. Required research behaviour

A normal address research run is iterative, not one-shot:

```text
address / point / territory
  ↓
initial research plan
  ↓
current entities around the location
  ↓
public web discovery
  ↓
factual retrieval
  ↓
new entities / old names / organisations / events / documents
  ↓
new search branches
  ↓
coverage-gap check
  ↓
additional searches
  ↓
stop only when the configured research budget is exhausted or no meaningful new branch remains
```

### 5.1 Territory research

For a radius around an address ARGUS must not research only the exact building. It must create a bounded inventory of relevant named entities in the territory and use those entities as additional research anchors.

Requested intents may include, among others:

- reviews;
- comments;
- complaints and public appeals;
- discussions and forums;
- public mentions;
- local news;
- incidents;
- historical context;
- explicit map/place categories;
- future universal intents added by consumers.

### 5.2 Recursive research

A source result may produce new factual entities. Those entities may create new navigation hypotheses, but they become facts only after a normal source is fetched and evidence is stored.

Example:

```text
"former factory X" found in a source
  ↓
factory X
  ↓
old addresses / owners / closure / demolition / reconstruction
  ↓
related documents / news / maps / photos
  ↓
subsequent use of the site
```

Recursive research must be bounded by depth, pages, query budget, deduplication and persistent checkpoints.

## 6. LLM role

The local LLM is part of the research engine, not a factual authority.

LLM may:

- create and refine search plans;
- identify missing research directions;
- propose follow-up queries;
- identify entities and navigation hypotheses;
- understand unfamiliar site interfaces;
- choose browser actions;
- help recover a broken SiteRecipe.

LLM must not:

- invent facts;
- turn its own prose into Evidence;
- choose a business conclusion for a consumer;
- bypass CAPTCHA or access controls.

Default local LLM backend is Ollama. Paid cloud LLMs must never be required for the base product.

## 7. Retrieval escalation

Every public web target follows the same escalation concept:

```text
FAST
  ↓ if insufficient
BROWSER
  ↓ if deterministic browser navigation is insufficient
AGENT
```

FAST is for ordinary HTML, XML, JSON, feeds, documents and public endpoints.

BROWSER is for JavaScript, SPA, lazy loading, forms, tabs, infinite/virtual scroll and other deterministic UI interaction.

AGENT is the last resort for an unknown interface. A successful agent route must be converted into a candidate SiteRecipe and verified by deterministic browser replay before it is trusted for reuse.

## 8. Source architecture

All sources implement the common SourceAdapter behaviour:

```text
discover → fetch/navigate → extract → normalize
```

Source adapters collect facts. They do not contain Kraken/Janus/Historical business logic.

ARGUS must support both universal formats and dedicated public-source adapters when a source needs stable special navigation.

Expected source families include:

- generic web;
- RSS/Atom/JSON Feed;
- HTML metadata and semantic structures;
- PDF and office documents;
- structured CSV/TSV/JSON/XML;
- maps/geospatial data;
- SERP/discovery providers;
- public map interfaces;
- review/discussion sources;
- official/municipal/public portals;
- historical archives, maps and photo archives.

## 9. SiteRecipe

When AGENT successfully learns a public-site route, ARGUS stores a versioned SiteRecipe only after deterministic BROWSER verification.

```text
agent exploration
  ↓
candidate recipe
  ↓
Playwright replay
  ↓ success
active SiteRecipe
```

Repeated failures invalidate the version. Interface change leads to new agent research and a new verified recipe version.

## 10. Historical intelligence

History is a core capability, not a separate afterthought.

ARGUS must combine:

- its own temporal snapshots;
- Wayback captures;
- old maps;
- historical photographs;
- archival catalogues and digitised documents;
- newspapers/publications;
- old names and organisations discovered during research;
- historical source-specific catalogues.

For a place, ARGUS must strive to construct an evidence-backed sequence of dated observations. It must preserve uncertainty and conflicting sources rather than fabricate a single narrative.

Historical output may contain:

- first/last observed state;
- appeared/disappeared between captures;
- changed name/operator/brand;
- construction/demolition/reconstruction mentions;
- historical map references;
- historical image references;
- archival documents and publications;
- links between current and historical entities.

ARGUS stores the evidence. A downstream Historical analytical module may interpret the timeline.

## 11. Images and visual historical evidence

Historical images are first-class factual references.

For a public image discovered on an archive/page, ARGUS should preserve at minimum:

- source page URL;
- image URL when publicly addressable;
- caption/alt/title when source-declared;
- date or date range when source-declared;
- place/entity relation when source-declared or explicitly established by the archive;
- author/collection/identifier when available;
- snapshot/provenance linking the reference to the fetched source page.

ARGUS must not infer a place/date merely from image pixels unless a future dedicated computer-vision capability explicitly produces a separately qualified inference.

## 12. Evidence rule

Every factual Observation must have traceable Evidence/provenance. Search snippets and LLM output are navigation, not Evidence.

The universal Observation model remains consumer-neutral and includes source, source_kind, URL, entity identity, text/data, geo, published/collected times, content hash, provenance and quality.

## 13. Historical/public source priority

For Russia, the Russian Empire and former USSR, `historical_context` must preferentially search the curated free/public sources maintained in `docs/HISTORICAL_SOURCES_RUSSIA_USSR.md`, in addition to generic web discovery and Wayback.

Source-specific scraping must respect public access restrictions and copyright/terms. A source may remain discovery-only if copying/downloading the underlying media is restricted.

## 14. Free base contour

The base ARGUS product must work without mandatory paid services.

Allowed base mechanisms include public HTML, browser parsing, RSS/Atom, public JSON/XML endpoints, open datasets, open-source software, and free/self-hosted services.

Paid SERP, paid proxy networks, paid CAPTCHA solving, commercial browser clouds, mandatory paid Google/Yandex/2GIS APIs and mandatory paid LLMs are not base dependencies.

CAPTCHA/access controls are never bypassed. ARGUS records blocked/partial coverage and continues through other public sources.

## 15. Storage and recovery

Server product storage is PostgreSQL; embedded standalone/test mode may use SQLite.

Persist at minimum:

- collections;
- observations;
- evidence;
- snapshots;
- coverage/errors;
- pending/visited checkpoint state;
- SiteRecipes;
- worker leases and operational state.

Crash/restart must resume from durable state without fabricating duplicate factual history.

## 16. API and module integration

ARGUS exposes an internal authenticated collection API. Consumers submit a collection and later read bounded/paginated results.

The API contract is consumer-neutral. The presence of `consumer` is for identity/provenance/idempotency and must not select hidden source logic.

Geo Analyzer owns service lifecycle; analytical modules own the decision to call ARGUS.

## 17. Standalone verification outside Geo Analyzer

ARGUS must always be testable independently of Geo Analyzer and analytical modules.

Required path:

```text
argus probe --address "..." --intent ...
```

Standalone probe uses the same production service graph in embedded mode with local SQLite. It must expose enough output to answer:

- what queries were planned;
- which sources were attempted;
- which pages/documents were fetched;
- which entities were discovered;
- which Observation/Evidence items were stored;
- what historical branches were created;
- what was blocked or missing;
- why the run stopped.

Probe is a product acceptance tool, not a mock implementation.

## 18. Security boundary

ARGUS is an internal backend, loopback-bound by default. It requires bearer authentication for server API, URL/redirect validation, SSRF protection, response/download limits, browser timeouts, safe logging, secret-file handling and bounded resource use.

Safety controls must not be weakened merely to make a difficult public source pass.

## 19. Definition of done

A product capability is done only when:

1. the intended end result works through the real service graph;
2. factual output has Evidence/provenance;
3. failure/recovery behaviour is defined;
4. resource and recursion limits are explicit;
5. standalone probe can demonstrate the result where applicable;
6. regression/integration tests cover the important path;
7. documentation reflects the actual runtime;
8. CI is green.

## 20. Governance

When code, README, architecture notes or a future task conflicts with this charter, this charter wins unless the product owner explicitly changes it.

The repository must not use "MVP" or "skeleton" as justification for omitting the original product behaviour. Incomplete work must be labelled incomplete and kept on the implementation plan until it is actually finished.
