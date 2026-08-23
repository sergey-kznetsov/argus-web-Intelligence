# Third-party foundation

ARGUS intentionally reuses maintained open-source components instead of reimplementing crawler and database infrastructure.

- Crawlee for Python: queue/request management, retries, sessions, concurrency and Playwright crawler runtime. Apache-2.0.
- Playwright: browser automation runtime. Apache-2.0.
- FastAPI: internal HTTP API. MIT.
- Psycopg 3 + psycopg_pool: PostgreSQL driver and native asyncio connection pool used by the product/server repository backend. LGPL-3.0-only. ARGUS uses the published package API and does not modify or vendor Psycopg code.
- defusedxml: hardened parsing for untrusted RSS/Atom and Sitemap XML, including entity/external-reference protections. Python Software Foundation License (PSFL).
- pypdf: local PDF metadata/text extraction. BSD-3-Clause. ARGUS requires `pypdf>=6.16.1,<7`, does not use a remote PDF service, and executes untrusted PDF parsing in a short-lived bounded child process. Raw document bytes remain subject to the normal HTTP response-size limit before parsing.
- Browser Use: optional agent backend with Ollama/local-model support. MIT.
- Ollama Python: optional local-model client. MIT.
- Stagehand through Crawlee: optional future/experimental agent backend. Verify the exact transitive Stagehand package/license before enabling it in a packaged distribution.
- SearXNG: optional separate/self-hosted discovery service accessed only through its HTTP API. AGPL-3.0-or-later. ARGUS does not vendor, link to, import or copy SearXNG code. If SearXNG is deployed, its own license and source-offer obligations must be handled for that separate service.
- DuckDuckGo HTML: optional low-volume public browser discovery fallback. ARGUS does not ship DuckDuckGo code or use a private API; search-result pages only provide candidate destination URLs and are never factual Evidence.
- OpenStreetMap/Overpass: optional map data provider accessed through a separately configured Overpass interpreter. OpenStreetMap data is licensed under ODbL and requires attribution. ARGUS normalizes each place with `© OpenStreetMap contributors`, the ODbL marker and a direct `openstreetmap.org` source URL. No public Overpass endpoint is enabled by default.
- Nominatim: optional address-to-coordinate provider accessed only through a separately configured HTTP endpoint. ARGUS does not enable the donated public OSMF Nominatim service by default. Geocoding candidates retain OpenStreetMap attribution/ODbL provenance and are used only to resolve map-search centers when coordinates were not supplied.
- Wayback CDX: optional exact-URL historical capture discovery through a separately configured CDX HTTP endpoint. ARGUS uses documented public CDX fields only and does not vendor Wayback code. A CDX row is evidence that an archive capture exists; page content is fetched separately from the concrete capture URL before it is treated as page Evidence. Archived page content retains the rights and access restrictions of the underlying source; ARGUS does not bypass archive access controls.

`robots.txt` and Sitemap support use the public protocol documents and site-published files directly; no third-party crawler code is copied for this feature.

Embedded JSON-LD support follows the W3C JSON-LD data model and `application/ld+json` media type. ARGUS parses only the JSON already embedded in a fetched page and never dereferences remote `@context` values.

No code is copied from these projects; ARGUS consumes published packages, documented HTTP interfaces or public browser pages.
