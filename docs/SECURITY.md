# Security notes

ARGUS follows OWASP ASVS-oriented controls appropriate to an internal evidence-collection service: Bearer authentication, loopback-only application exposure, SSRF and outbound-policy validation, bounded resources, safe error boundaries, structured secret-safe logging, isolated secret files, API rate limiting, conservative response headers, and browser containment controls.

## URL, SSRF and egress boundary

Arbitrary crawl targets are restricted to HTTP(S), may not contain URL userinfo, and must resolve only to public addresses unless the exact hostname/IP is explicitly allowlisted as an internal target.

`UrlGuard` additionally enforces two operator policies for arbitrary crawl targets:

- `ARGUS_OUTBOUND_PUBLIC_PORTS`, default `80,443`;
- `ARGUS_DENY_OUTBOUND_HOSTS`, which blocks the configured host and all its subdomains.

The explicit deny-list has priority over the internal allowlist. An explicitly allowlisted internal target may use a non-public port because that exception is operator controlled. Every redirect is revalidated, so an accepted public URL cannot redirect into a denied/private/blocked-port target.

FAST uses a guarded Crawlee `HttpxHttpClient`: an async HTTPX request hook validates the initial request and every redirect hop before the transport sends that hop. Final URLs are validated again as defense in depth. FAST disables environment-proxy inheritance and caps redirect count.

BROWSER applies the same `UrlGuard` to page subrequests and validates the final page URL. Unsafe private/link-local/reserved/cloud-metadata, denied-host and disallowed-port requests are aborted.

Application URL validation is deliberately not presented as a complete network-security boundary. TEST/PROD deployment must also enforce outbound firewall/network-namespace rules, especially for cloud metadata and private infrastructure ranges. Application policy is defense in depth; the host/network layer is authoritative against browser/runtime compromise.

## API boundary

ARGUS refuses an application configuration whose `ARGUS_HOST` is not loopback (`127.0.0.1`, `::1`, or `localhost`). The universal Geo Analyzer module manifest also starts both API and worker probe on `127.0.0.1`.

The `/v1/*` API receives conservative headers:

- `Cache-Control: no-store`;
- `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'`;
- `Cross-Origin-Resource-Policy: same-origin`;
- `Permissions-Policy` disabling camera, microphone, geolocation and payment;
- `Referrer-Policy: no-referrer`;
- `X-Content-Type-Options: nosniff`;
- `X-Frame-Options: DENY`.

HSTS is intentionally not emitted by ARGUS because the internal service is localhost HTTP by default. TLS/HSTS belong to a reverse proxy if the deployment architecture introduces one.

`ClientRateLimitMiddleware` protects the API process with a bounded token bucket. It is configured by:

- `ARGUS_API_RATE_LIMIT_REQUESTS_PER_MINUTE`;
- `ARGUS_API_RATE_LIMIT_BURST`.

The limiter uses only the direct TCP peer address and ignores `X-Forwarded-For` and similar headers. This prevents an unauthenticated caller from selecting its own limiter key. `/v1/health` is exempt so service orchestration remains available. PostgreSQL queue admission limits remain the authoritative per-consumer backpressure control.

Request bodies are independently bounded by `RequestSizeLimitMiddleware`, including chunked bodies that omit `Content-Length`.

## Browser containment

The Crawlee/Playwright runtime explicitly requests Chromium with its process sandbox enabled. ARGUS does not add `--no-sandbox`.

Each page uses an isolated incognito browser context. Context policy disables downloads and service workers and does not ignore TLS certificate errors. Browser concurrency, navigation/handler timeout, total response bytes and request rate remain bounded by ARGUS settings.

Every HTTP(S) browser subrequest passes through the same application URL/egress guard used by the rest of the crawl stack.

These controls reduce cross-request state and browser attack surface, but Chromium still executes untrusted public JavaScript. TEST/PROD must therefore run the worker under an unprivileged OS identity and add process/container isolation, filesystem restrictions, cgroup/resource quotas and network egress rules. ARGUS application code cannot replace those host-level controls.

## AGENT boundary

AGENT is disabled by default and remains last-resort only. Browser Use is bounded by steps, actions, history, runtime and domain scope. External search and filesystem tools are disabled. It must stop on CAPTCHA/access-control challenges and cannot log in, create accounts, upload files, purchase, or submit state-changing forms.

AGENT output itself is never Evidence. Reusable actions must compile to a deterministic SiteRecipe and pass BROWSER replay before persistence/use as a verified path.

## Secrets and errors

Bearer tokens are generated with cryptographic randomness and persisted atomically. On POSIX, token files are hardened and verified as owner-only before use, including existing token files.

When a PostgreSQL DSN secret file is configured, ARGUS verifies that it exists before startup and, on POSIX, hardens it to owner-only permissions. Windows uses ACL semantics that cannot be reliably represented by POSIX mode-bit checks; the module manager remains responsible for Windows ACLs.

Structured API errors and ARGUS JSON logs redact Bearer values, common token/password/API-key assignments and URL query strings. Full arbitrary request URLs are not emitted as orchestrator log fields. Operational metrics additionally reject request-specific identifiers as labels.

## Resource controls

Response size, HTTP/browser timeouts, redirect count, global crawler concurrency, browser concurrency, request rates, document parser sizes, JSON/XML/OOXML node budgets and result-delivery page sizes are bounded.

Selected domains can use Crawlee throttling for per-domain delay and 429 backoff behavior. Direct providers use bounded retries and rate gates.

CAPTCHA and access-control challenges are not bypassed. They are represented as blocked/partial coverage and ARGUS may continue only with other independently public sources.

## Dependency and supply-chain controls

CI runs:

- `pip check` to reject an inconsistent installed dependency graph;
- pinned `pip-audit==2.10.1` with `pip-audit --local` to fail on known vulnerabilities in the installed environment;
- Python compilation;
- Ruff static checks;
- the PostgreSQL-backed and embedded pytest suites.

Runtime dependencies remain version-bounded in `pyproject.toml`. Browser Use, which has a faster-moving API surface, is kept within the validated `0.13.x` line and is optional because AGENT is disabled by default.

CI vulnerability scanning is not a substitute for dependency review, lock/reproducibility policy, repository protections, signed releases or host image scanning. Those are deployment/release controls.

## What remains deployment-level

ARGUS intentionally does not claim to implement an OS/container sandbox or firewall from inside its Python process. Before TEST is promoted to PROD, the deployment must verify:

- unprivileged service account;
- minimal filesystem permissions for source, runtime and secrets;
- outbound firewall/network namespace denying private/metadata networks except explicitly required internal services;
- Chromium sandbox actually available on the host;
- process/cgroup CPU, memory and PID limits;
- TLS/reverse-proxy policy if ARGUS is ever exposed beyond loopback;
- secret rotation and backup/restore access controls;
- dependency/image scanning in the deployment pipeline.

Threats intentionally unsupported by ARGUS include CAPTCHA bypass, authenticated access-control bypass, paid proxy rotation, commercial anti-bot evasion and access to private resources without explicit operator configuration.
