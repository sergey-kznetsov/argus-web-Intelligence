# Security notes

The implementation follows OWASP ASVS-oriented controls appropriate to this internal service: Bearer authentication, localhost-first exposure, SSRF validation, resource limits, safe error boundaries, structured secret-safe logging and secret separation.

## URL and SSRF boundary

Arbitrary crawl targets are restricted to HTTP(S), may not contain URL userinfo and must resolve only to public addresses unless the exact hostname/IP is explicitly allowlisted as an internal target.

FAST uses a guarded Crawlee `HttpxHttpClient`: an async HTTPX request hook validates the initial request and every redirect hop before the transport sends that hop. Final URLs are validated again as defense in depth. FAST also disables environment-proxy inheritance and caps redirect count.

BROWSER uses a Playwright route guard for page network requests. Unsafe private/link-local/reserved/cloud-metadata requests are aborted. The final page URL is validated again after navigation.

Application URL validation is not a complete network security boundary. Production deployment must also restrict outbound network access with firewall/egress rules, especially cloud metadata and private infrastructure ranges.

## Secrets and errors

Bearer tokens are generated with cryptographic randomness, persisted atomically and use restrictive file permissions where the operating system supports them. Empty or implausibly short token files are replaced rather than accepted as valid credentials.

Structured API errors and ARGUS JSON logs redact Bearer values, common token/password/API-key assignments and URL query strings. Full arbitrary request URLs are not emitted as orchestrator log fields.

## Resource controls

Response size, HTTP/browser timeouts, redirect count, global crawler concurrency, browser concurrency and request rates are configurable. Selected domains can use Crawlee's native throttling request manager for per-domain delay and 429 backoff behavior.

CAPTCHA and access-control challenges are not bypassed. They are represented as blocked/partial coverage and ARGUS may continue with other public sources.

## Deployment controls

Threats intentionally not solved by ARGUS include CAPTCHA bypass, authenticated access-control bypass, paid proxy rotation and commercial anti-bot evasion.

Before exposing ARGUS beyond localhost, add reverse-proxy/TLS controls, network egress rules, secret rotation, process sandboxing, browser OS isolation and deployment-level resource quotas.
