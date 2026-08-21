# ARGUS Web Intelligence

ARGUS is an internal evidence-first web intelligence backend. It discovers, fetches, navigates, extracts, normalizes and stores public-source data for analytical consumers such as Kraken, Janus and future modules. It does not contain their business logic and is not a Geo Analyzer checkbox/module.

Core rule: **ARGUS = find + obtain + prove + store. Consumers = interpret + calculate + conclude.**

## Milestone 1

The service includes versioned contracts, asynchronous collections, SQLite storage behind a repository abstraction, Crawlee FAST/BROWSER runtimes, Generic Web and RSS/Atom adapters, Research Planner with optional local Ollama, recursive research tasks, agent abstraction, SiteRecipe persistence/replay primitives, SHA-256 snapshots/diffs, Bearer authentication, URL/redirect SSRF validation, limits/timeouts, cancellation, resume/checkpoints, CLI, tests and CI.

Specific map/SERP/public-portal adapters are intentionally deferred.

## Install

Python 3.11+ is required. Python 3.12 is used in CI.

```bash
python -m venv .venv
. .venv/bin/activate              # Windows: .venv\\Scripts\\activate
pip install -e '.[dev]'
playwright install chromium
```

Create a token once:

```bash
argus init-token
```

Run:

```bash
argus serve
```

By default ARGUS binds to `127.0.0.1:8787`.

## CLI

```bash
argus collect --consumer test --address "Ижевск, Пушкинская, 277" --intent public_mentions
argus status <collection_id>
argus result <collection_id>
argus sources
```

All API endpoints except `/v1/health` require `Authorization: Bearer <token>`.

## API

- `GET /v1/health`
- `GET /v1/capabilities`
- `POST /v1/collections`
- `GET /v1/collections/{collection_id}`
- `GET /v1/collections/{collection_id}/result`
- `POST /v1/collections/{collection_id}/cancel`
- `GET /v1/sources`
- `GET /v1/sources/{source_id}/health`

`POST /v1/collections` returns `202 Accepted`. Work continues asynchronously and survives process restarts through persisted task/checkpoint state.

## Security boundary

ARGUS rejects arbitrary targets that resolve to loopback, private, link-local, multicast, reserved or cloud-metadata addresses unless an explicit internal-target allowlist permits them. Redirect targets are validated again. Response and browser time limits are enforced. Secrets are never included in structured errors.

Application-level SSRF validation is defense in depth. Production deployment should additionally restrict egress at the firewall/network layer.

CAPTCHA and access-control challenges are not bypassed. They are reported as blocked/partial coverage.

## Agent backends

The core has an `AgentBackend` interface. Browser Use + Ollama is the default optional local-agent integration. Stagehand support is isolated behind an optional backend because its Python/local-model integration can evolve independently of the ARGUS core. Neither paid APIs nor browser clouds are required.

## Storage

Milestone 1 uses SQLite. The orchestrator depends on the repository protocol rather than SQLite directly, so PostgreSQL can be added later without rewriting collection logic.

## Development rules

Keep source adapters factual. No scoring, risk calculations or module-specific branches belong in ARGUS. Prefer existing open-source crawling/runtime primitives over duplicating queues, retries, sessions or concurrency management.
