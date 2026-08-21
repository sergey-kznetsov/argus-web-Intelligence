# Third-party foundation

ARGUS intentionally reuses maintained open-source components instead of reimplementing crawler infrastructure.

- Crawlee for Python: queue/request management, retries, sessions, concurrency and Playwright crawler runtime. Apache-2.0.
- Playwright: browser automation runtime. Apache-2.0.
- FastAPI: internal HTTP API. MIT.
- Browser Use: optional agent backend with Ollama/local-model support. MIT.
- Ollama Python: optional local-model client. MIT.
- Stagehand through Crawlee: optional future/experimental agent backend. Verify the exact transitive Stagehand package/license before enabling it in a packaged distribution.
- SearXNG: optional separate/self-hosted discovery service accessed only through its HTTP API. AGPL-3.0-or-later. ARGUS does not vendor, link to, import or copy SearXNG code. If SearXNG is deployed, its own license and source-offer obligations must be handled for that separate service.

No code is copied from these projects; ARGUS consumes published packages or documented HTTP interfaces.
