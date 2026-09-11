# Безопасность ARGUS

ARGUS применяет security controls, соответствующие внутреннему evidence-collection service: Bearer auth, loopback-only exposure, SSRF/outbound validation, bounded resources, safe error boundaries, secret-safe structured logging, isolated secret files, API rate limiting, conservative response headers и browser containment.

## URL, SSRF и egress

Arbitrary crawl targets ограничены HTTP(S), URL userinfo запрещён. Target должен разрешаться только в public address, если exact hostname/IP не включён оператором в internal allowlist.

`UrlGuard` применяет:

```text
ARGUS_OUTBOUND_PUBLIC_PORTS   default 80,443
ARGUS_DENY_OUTBOUND_HOSTS
```

Deny-list имеет приоритет над internal allowlist. Explicit allowlisted internal target может использовать другой port как осознанное operator exception.

Каждый redirect проверяется заново.

FAST использует guarded Crawlee `HttpxHttpClient`: HTTPX hook валидирует initial request и каждый redirect hop до отправки. Environment proxy inheritance отключён, redirect count bounded.

BROWSER применяет тот же `UrlGuard` к HTTP(S) subrequests и final page URL; unsafe private/link-local/reserved/cloud-metadata, denied hosts и disallowed ports блокируются.

Application guard — defense in depth. Host/network egress policy остаётся обязательной инфраструктурной границей для server deployment.

## API boundary

ARGUS не запускается с `ARGUS_HOST`, который не является loopback (`127.0.0.1`, `::1`, `localhost`). Standalone deployment также привязывает API/worker probe к loopback.

`/v1/*` responses получают conservative headers:

```text
Cache-Control: no-store
Content-Security-Policy: default-src 'none'; frame-ancestors 'none'
Cross-Origin-Resource-Policy: same-origin
Permissions-Policy: camera/microphone/geolocation/payment disabled
Referrer-Policy: no-referrer
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
```

HSTS не отдаётся самим ARGUS, потому что internal API по умолчанию localhost HTTP. TLS/HSTS принадлежат reverse proxy, если архитектура когда-либо вводит внешний ingress.

`ClientRateLimitMiddleware` использует bounded token bucket:

```text
ARGUS_API_RATE_LIMIT_REQUESTS_PER_MINUTE
ARGUS_API_RATE_LIMIT_BURST
```

Limiter использует direct TCP peer и игнорирует `X-Forwarded-For`. `/v1/health` exempt для orchestration. Per-consumer authoritative backpressure обеспечивается PostgreSQL queue admission.

Request body независимо ограничивает `RequestSizeLimitMiddleware`, включая chunked requests без `Content-Length`.

## Browser containment

Playwright Chromium запускается с sandbox; ARGUS не добавляет `--no-sandbox`.

Каждая page использует isolated browser context. Downloads/service workers отключены, TLS errors не игнорируются. Concurrency/time/response bytes/request rate bounded.

Все HTTP(S) browser subrequests проходят URL/egress guard.

Chromium всё равно исполняет недоверенный public JavaScript, поэтому production host должен использовать минимально привилегированный service identity, filesystem isolation, resource limits и network egress restrictions. Текущий Windows standalone deployment запускает Scheduled Tasks как SYSTEM; это операционный риск относительно целевой least-privilege posture и не следует выдавать за полностью изолированную execution identity.

## AGENT boundary

AGENT подключён как необязательный третий уровень после FAST/BROWSER. Model output не Evidence. Auto fallback выполняется последовательно Recipe → Stagehand → Browser Use через единый LLM gate с параллелизмом 1.

Recipe backend выбирает только controls и значения, подготовленные ARGUS. Stagehand получает инертный DOM snapshot без scripts, event handlers, meta refresh, inline external styles и внешних subrequests. Browser Use работает в отдельном venv, ограничен allowed domains, шагами, временем, историей и размером результата; файловые tools/downloads отключены. Любой путь повторно проходит deterministic SiteRecipe/BROWSER replay и `UrlGuard`.

CAPTCHA/login/access-control/paywall/payment/state-changing actions не обходятся. Blocked result прекращает fallback, а не запускает другой backend.

## Secrets и errors

Bearer tokens генерируются cryptographically random и записываются atomically. На POSIX token/DSN files проверяются и hardened как owner-only. На Windows безопасность обеспечивается ACL; `deploy-server.ps1` ограничивает `secrets` SYSTEM и Administrators.

API errors и JSON logs редактируют Bearer values, common token/password/API-key assignments и URL query strings. Operational metrics запрещают request-specific IDs/URLs как labels.

## Resource controls

Bounded:

- HTTP response bytes;
- API body size;
- HTTP/browser timeouts;
- redirect count;
- crawler/browser concurrency;
- request rates;
- PDF/structured/OOXML sizes;
- JSON/XML node/depth limits;
- result page sizes;
- retries и recursive research.

Selected domains могут использовать per-domain throttling. Direct providers используют bounded retries/rate gates.

CAPTCHA/access-control challenge не обходится; источник получает blocked/partial state, а research может продолжиться только через независимо публичные источники.

## Supply chain

CI включает:

```text
pip check
pip-audit --local
Python compilation
Ruff
PostgreSQL-backed и embedded pytest
```

Dependencies version-bounded в `pyproject.toml`. Stagehand и Browser Use проверяются отдельными dependency-профилями с `pip check`, потому что их обязательные версии `websockets` несовместимы в одном venv.

CI scanning не заменяет dependency review, release protections и host/image scanning.

## Что остаётся deployment-level

Перед production эксплуатацией должны отдельно проверяться:

- минимально привилегированный service account;
- filesystem permissions;
- outbound firewall/network isolation;
- доступность Chromium sandbox;
- CPU/memory/PID limits;
- TLS/reverse proxy, если есть внешний ingress;
- secret rotation;
- backup/restore access control;
- dependency/image scanning.

ARGUS не поддерживает CAPTCHA bypass, authenticated access-control bypass, paid proxy rotation или скрытый доступ к private resources без explicit operator configuration.
