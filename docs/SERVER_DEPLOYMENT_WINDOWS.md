# Standalone ARGUS deployment on Windows Server

ARGUS is a server-level infrastructure service. It is not a Geo Analyzer installable module and must not appear in the Geo Analyzer module lifecycle or analysis-launch UI.

The runtime topology is:

```text
Windows Server
  ├─ ARGUS API       127.0.0.1:8787
  ├─ ARGUS worker    127.0.0.1:8788 readiness probe
  ├─ Geo Analyzer TEST
  │    └─ Kraken ───────────────┐
  └─ Geo Analyzer PROD          │
       └─ Kraken ───────────────┤
                                ↓
                         standalone ARGUS
```

Geo Analyzer does not start, stop, reinstall, update or delete ARGUS. Consumer modules inherit the two generic server variables from the Geo Analyzer process environment:

```text
ARGUS_SERVICE_BASE_URL=http://127.0.0.1:8787
ARGUS_SERVICE_TOKEN_FILE=C:\ProgramData\ARGUS\secrets\argus.token
```

Kraken prefers these generic names. Its old `KRAKEN_ARGUS_*` variables remain only as a temporary backwards-compatible fallback.

## Server layout

```text
C:\argus\releases\<commit>\        immutable application release
C:\ProgramData\ARGUS\argus.env    service configuration
C:\ProgramData\ARGUS\secrets\    bearer token and PostgreSQL DSN
C:\ProgramData\ARGUS\logs\       API and worker logs
C:\ProgramData\ARGUS\deployment.json
```

Two SYSTEM scheduled tasks provide the single logical service:

```text
ARGUS-API
ARGUS-Worker
```

Both are configured to start at boot and restart after process failure. The API and worker bind only to loopback; there is no public ARGUS ingress.

## Deployment safety

`deploy/windows/deploy-server.ps1` is plan-only unless `-Apply` is supplied. Apply performs these steps:

1. resolves an exact GitHub commit;
2. builds a new immutable release and isolated Python 3.11 virtual environment;
3. installs Chromium for the Playwright path;
4. preserves the server bearer token and copies the PostgreSQL DSN into an ARGUS-owned secret file;
5. runs ARGUS schema migrations and schema verification before cutover;
6. stops the previous ARGUS tasks and points them at the new release;
7. starts worker and API and waits for both readiness checks;
8. rolls the tasks back to the previous release if the new release does not become healthy;
9. keeps the current and previous releases for rollback.

ARGUS owns only the `argus` PostgreSQL schema. It may use the same PostgreSQL instance/database as Geo Analyzer while remaining a separate application lifecycle.

## Deploy

Plan:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File deploy\windows\deploy-server.ps1 `
  -Ref main
```

Apply:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File deploy\windows\deploy-server.ps1 `
  -Ref main `
  -Apply
```

Default production database configuration source:

```text
C:\ProgramData\GeoAnalyzer\saas.env
```

The deployment copies only the resolved DSN value into `C:\ProgramData\ARGUS\secrets\database-dsn.txt`; runtime ARGUS does not depend on reading Geo Analyzer's environment file.

## Health

```powershell
Invoke-RestMethod http://127.0.0.1:8787/v1/health
Invoke-RestMethod http://127.0.0.1:8788/readyz
Get-ScheduledTask -TaskName "ARGUS-*" | Select-Object TaskName,State
```

The API is considered ready only when PostgreSQL is healthy and a worker is active.

## Connect Geo Analyzer TEST

Run plan first:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File C:\argus\releases\<commit>\deploy\windows\configure-geo-analyzer-consumer.ps1 `
  -EnvironmentFile C:\ProgramData\GeoAnalyzerTest\saas.env `
  -TaskName GeoAnalyzerTest
```

Then apply the same command with `-Apply`.

After Geo Analyzer TEST restarts, install or reinstall only Kraken through the Geo Analyzer module interface. Kraken inherits the generic ARGUS service variables from Geo Analyzer and calls the already-running standalone service.

## Connect Geo Analyzer PROD

Production is configured only after the same Kraken flow passes in TEST. Use the same consumer script with:

```text
EnvironmentFile = C:\ProgramData\GeoAnalyzer\saas.env
```

and the production Geo Analyzer scheduled-task name. No separate production ARGUS instance is created: TEST and PROD consume the same standalone service through localhost.

## Upgrade ARGUS

Run `deploy-server.ps1` again with the desired `-Ref` and `-Apply`. Geo Analyzer and Kraken do not need reinstall merely because ARGUS is upgraded, provided the protocol remains compatible. ARGUS cutover and rollback remain entirely inside the standalone service lifecycle.
