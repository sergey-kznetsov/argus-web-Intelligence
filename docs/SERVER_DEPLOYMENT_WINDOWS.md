# Развёртывание standalone ARGUS на Windows Server

ARGUS — server-level infrastructure service. Он не устанавливается через Module Manager Geo Analyzer.

```text
Windows Server
  ├─ ARGUS API     127.0.0.1:8787
  ├─ ARGUS Worker  127.0.0.1:8788 readiness probe
  ├─ Geo Analyzer TEST -> modules -> ARGUS
  └─ Geo Analyzer PROD -> modules -> ARGUS
```

## Server layout

```text
C:\argus\releases\<commit>\
C:\ProgramData\ARGUS\argus.env
C:\ProgramData\ARGUS\secrets\argus.token
C:\ProgramData\ARGUS\secrets\database-dsn.txt
C:\ProgramData\ARGUS\secrets\github-token.txt   # только если нужен GitHub auth
C:\ProgramData\ARGUS\logs\
C:\ProgramData\ARGUS\deployment.json
```

Scheduled Tasks:

```text
ARGUS-API
ARGUS-Worker
```

Обе задачи запускаются как SYSTEM, стартуют при загрузке и перезапускаются после process failure.

## PostgreSQL

`deploy/windows/deploy-server.ps1` **не читает** `C:\server\saas.env`, TEST `saas.env` или другой Geo Analyzer environment file для получения БД.

Перед `-Apply` должен существовать ARGUS-owned файл:

```text
C:\ProgramData\ARGUS\secrets\database-dsn.txt
```

Deploy проверяет DSN и прерывается, если:

```text
dbname != argus
user   != argus
```

Следовательно, канонический standalone deployment использует отдельную PostgreSQL database `argus`, service role `argus` и schema `argus`. PostgreSQL server как инфраструктура может быть тем же физическим сервером, но database/login/lifecycle ARGUS изолированы от Geo Analyzer.

## Plan и Apply

`-Ref` должен быть точным 40-символьным Git commit SHA. Branch name `main` для deploy запрещён.

```powershell
$Ref = "<40-character-CI-green-commit-SHA>"

powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File deploy\windows\deploy-server.ps1 `
  -Ref $Ref
```

Plan-only ничего не меняет.

После проверки плана:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File deploy\windows\deploy-server.ps1 `
  -Ref $Ref `
  -Apply
```

Apply:

1. проверяет commit через GitHub;
2. скачивает immutable snapshot;
3. создаёт основной Python 3.11 venv со Stagehand и отдельный `.venv-browser-use`;
4. выполняет `pip check` в обоих окружениях и устанавливает Chromium;
5. проверяет ARGUS-owned DB identity;
6. создаёт/preserves Bearer token;
7. формирует managed `argus.env`;
8. запускает migrations + schema check;
9. переключает `ARGUS-Worker` и `ARGUS-API`;
10. ждёт worker/API readiness;
11. проверяет, что listeners принадлежат новой release;
12. при failure возвращает Scheduled Tasks на previous release.

## Ollama и AGENT

Перед первым запуском настройте локальный Ollama из elevated PowerShell:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File deploy\windows\tune-ollama-cpu.ps1
```

Скрипт фиксирует один параллельный запрос, одну загруженную модель, очередь 4, `keep_alive=60s`, создаёт `argus-qwen3:8b-cpu` с `num_ctx=4096` и `num_predict=512`, а процессу Ollama назначает `BelowNormal` и affinity на два CPU по умолчанию.

Deployment включает LLM как optional control layer (`ARGUS_LLM_REQUIRED=false`). Основной venv содержит Stagehand; Browser Use запускается через `ARGUS_BROWSER_USE_PYTHON` из отдельного окружения. Недоступность Ollama отражается как degraded LLM check, но не останавливает deterministic collection path.

## Health

```powershell
Invoke-RestMethod http://127.0.0.1:8787/v1/health
Invoke-RestMethod http://127.0.0.1:8788/readyz
Get-ScheduledTask -TaskName "ARGUS-*" | Select-Object TaskName,State
```

API readiness для role=`api` требует PostgreSQL + свежий worker heartbeat.

## Подключение Geo Analyzer

Consumers получают:

```text
ARGUS_SERVICE_BASE_URL=http://127.0.0.1:8787
ARGUS_SERVICE_TOKEN_FILE=C:\ProgramData\ARGUS\secrets\argus.token
```

Для TEST helper:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File C:\argus\releases\<commit>\deploy\windows\configure-geo-analyzer-consumer.ps1 `
  -EnvironmentFile C:\ProgramData\GeoAnalyzerTest\saas.env `
  -TaskName GeoAnalyzerTest
```

Сначала plan, затем та же команда с `-Apply`.

Для PROD helper применяется к фактическому production environment и Scheduled Task только после успешной TEST-интеграции. Helper записывает лишь `ARGUS_SERVICE_BASE_URL` и `ARGUS_SERVICE_TOKEN_FILE`; он не переносит PostgreSQL credentials.

## Обновление

ARGUS обновляется отдельным запуском `deploy-server.ps1` с новым CI-green commit SHA. Geo Analyzer/Kraken не должны переустанавливаться только из-за совместимого обновления ARGUS protocol.

Нельзя выводить Bearer token или DB DSN в диагностике.
