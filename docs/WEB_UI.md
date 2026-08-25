# Standalone Web UI

ARGUS remains a headless AI web-intelligence backend. The optional web interface is a separate operator gateway, not a second crawler or a second orchestration path.

Architecture:

```text
Browser
  -> ARGUS Web UI gateway (argus-web, separate port)
  -> local ARGUS API (/v1/collections)
  -> Research Planner
  -> FAST -> BROWSER -> AGENT
  -> Source adapters / Evidence / Snapshots / Storage

Geo Analyzer modules
  -> the same local ARGUS API
```

The gateway therefore cannot bypass the normal CollectionRequest contract, Research Planner, source registry, budgets, security policy, provenance or persistence rules.

## Start

First start the normal ARGUS API. For a standalone SQLite installation this can run in embedded mode:

```bash
ARGUS_EXECUTION_ROLE=embedded \
ARGUS_STORAGE_BACKEND=sqlite \
ARGUS_HOST=127.0.0.1 \
ARGUS_PORT=8787 \
argus serve
```

Then start the web gateway:

```bash
ARGUS_WEB_API_URL=http://127.0.0.1:8787 \
ARGUS_WEB_API_TOKEN_FILE=.argus/token \
ARGUS_WEB_HOST=127.0.0.1 \
ARGUS_WEB_PORT=8790 \
argus-web
```

Open `http://127.0.0.1:8790/`.

For an ARGUS installation managed beside Geo Analyzer, point `ARGUS_WEB_API_URL` at the localhost ARGUS API port assigned to that installation. The web gateway does not need a new database and does not replace the module/API process.

## Authentication

The browser never receives the internal ARGUS bearer token. `argus-web` reads that token from `ARGUS_WEB_API_TOKEN_FILE` and adds it only to server-side requests to the local ARGUS API.

The web interface itself is protected with HTTP Basic authentication. Username defaults to `argus`. On first start a random password is generated in `.argus/web-password` (configurable with `ARGUS_WEB_PASSWORD_FILE`). The password is not printed to logs.

Do not expose the UI directly over plain HTTP on a public network. For remote access, keep the process on localhost and publish it through an HTTPS reverse proxy with appropriate network access controls.

## Security boundary

The gateway is intentionally not a generic reverse proxy. It only exposes a fixed list of ARGUS operations required by the UI:

- health and capabilities;
- source list;
- submit collection;
- collection status;
- cancel collection;
- result summary;
- paged observations;
- paged evidence.

`ARGUS_WEB_API_URL` is restricted to loopback hosts (`127.0.0.1`, `localhost`, `::1`) to prevent the operator UI from becoming an SSRF primitive.

The UI returns a restrictive Content-Security-Policy, disables framing, uses `no-store`, and renders result payloads as text rather than injecting returned HTML.

## Product boundary

The web UI is an additional consumer of ARGUS. It must not contain source-specific scraping logic, research heuristics, module business logic or independent storage. Any capability needed by both the UI and Geo Analyzer consumers belongs in the core ARGUS API/orchestrator instead.
