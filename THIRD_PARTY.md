# Third-party foundation

ARGUS intentionally reuses maintained open-source components instead of reimplementing crawler infrastructure.

- Crawlee for Python: queue/request management, retries, sessions, concurrency and Playwright crawler runtime. Apache-2.0.
- Playwright: browser automation runtime. Apache-2.0.
- FastAPI: internal HTTP API. MIT.
- Browser Use: optional agent backend with Ollama/local-model support. MIT.
- Ollama Python: optional local-model client. MIT.
- Stagehand through Crawlee: optional future/experimental agent backend. Verify the exact transitive Stagehand package/license before enabling it in a packaged distribution.
- SearXNG: optional separate/self-hosted discovery service accessed only through its HTTP API. AGPL-3.0-or-later. ARGUS does not vendor, link to, import or copy SearXNG code. If SearXNG is deployed, its own license and source-offer obligations must be handled for that separate service.
- DuckDuckGo HTML: optional low-volume public browser discovery fallback. ARGUS does not ship DuckDuckGo code or use a private API; search-result pages only provide candidate destination URLs and are never factual Evidence.
- OpenStreetMap/Overpass: optional map data provider accessed through a separately configured Overpass interpreter. OpenStreetMap data is licensed under ODbL and requires attribution. ARGUS normalizes each place with `© OpenStreetMap contributors`, the ODbL marker and a direct `openstreetmap.org` source URL. No public Overpass endpoint is enabled by default.
- Nominatim: optional address-to-coordinate provider accessed only through a separately configured HTTP endpoint. ARGUS does not enable the donated public OSMF Nominatim service by default. Geocoding candidates retain OpenStreetMap attribution/ODbL provenance and are used only to resolve map-search centers when coordinates were not supplied.

No code is copied from these projects; ARGUS consumes published packages, documented HTTP interfaces or public browser pages.
