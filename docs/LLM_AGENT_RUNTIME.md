# Локальный LLM/AGENT-контур ARGUS

## Назначение

Ollama — необязательный управляющий слой ARGUS. Модель помогает построить план исследования, предложить follow-up, оценить достаточность покрытия, выдвинуть гипотезы сущностей, выбрать точную цитату и найти безопасный навигационный путь. Вывод модели никогда не считается `Observation` или `Evidence`.

Факт принимается только после получения публичного источника обычным crawler runtime. Для семантического Evidence цитата должна буквально входить в текст уже полученного источника. Для AGENT-действий обязательны компиляция в ограниченный `SiteRecipe` и независимый Playwright replay.

## Порядок выполнения

1. Детерминированные правила формируют безопасную основу плана.
2. LLM planner может предложить ограниченное дополнение; ошибочный ответ отбрасывается.
3. Источник обрабатывается через `FAST`.
4. `BROWSER` запускается только когда статического ответа недостаточно.
5. `AGENT` запускается только после неудачи обычного BROWSER либо по явному контракту source adapter для публичного фильтра/раскрытия.
6. В режиме `auto` backends пробуются строго последовательно: `ollama-recipe`, `stagehand`, `browser-use`.
7. Если источник показывает CAPTCHA или access challenge, fallback прекращается: другой backend не используется для обхода ограничения.
8. Предложенные действия компилируются в разрешённые шаги и выполняются Playwright.
9. Recipe становится активным только после source-backed проверки исследовательской цели.

## Общий ресурсный профиль

Один worker использует единый `LlmConcurrencyGate` для planner, follow-up planner, supervisor, entity hypotheses, semantic classifier, Recipe, Stagehand и Browser Use. Значение по умолчанию — один одновременный вызов.

```text
ARGUS_LLM_ENABLED=true
ARGUS_LLM_REQUIRED=false
ARGUS_OLLAMA_URL=http://127.0.0.1:11434
ARGUS_OLLAMA_MODEL=argus-qwen3:8b-cpu
ARGUS_OLLAMA_NUM_THREAD=2
ARGUS_OLLAMA_NUM_CTX=4096
ARGUS_OLLAMA_NUM_PREDICT=512
ARGUS_OLLAMA_KEEP_ALIVE_SECONDS=60
ARGUS_LLM_MAX_CONCURRENCY=1
ARGUS_LLM_REQUEST_TIMEOUT_SECONDS=20
```

Ollama daemon настраивается отдельно:

```text
OLLAMA_NUM_PARALLEL=1
OLLAMA_MAX_LOADED_MODELS=1
OLLAMA_MAX_QUEUE=4
OLLAMA_KEEP_ALIVE=60s
```

На Windows используйте elevated PowerShell:

```powershell
.\deploy\windows\tune-ollama-cpu.ps1
```

Скрипт создаёт производную модель `argus-qwen3:8b-cpu`, сохраняет daemon limits, запускает Ollama с `BelowNormal` priority и ограничивает processor affinity двумя логическими CPU по умолчанию. Число потоков ARGUS можно изменить через `ARGUS_OLLAMA_NUM_THREAD`, а affinity — параметром `-CpuCount`.

## Stagehand и Browser Use

Stagehand 4 и Browser Use 0.13.10 нельзя устанавливать в один Python venv: первый требует `websockets >=16.1.1`, второй через `browser-harness` фиксирует `websockets ==15.0.1`. Поэтому поддерживаются два профиля:

```bash
pip install -e '.[stagehand]'
pip check

python -m venv .venv-browser-use
.venv-browser-use/Scripts/python -m pip install -e '.[agent-browser-use]'
.venv-browser-use/Scripts/python -m pip check
```

`ARGUS_BROWSER_USE_PYTHON` указывает на Python второго окружения. Browser Use запускается как ограниченный дочерний процесс без shell; вход и выход имеют лимиты, результат повторно преобразуется в строгий `AgentResult`. Родительский worker удерживает общий LLM semaphore до завершения дочернего процесса. Windows deployment создаёт и проверяет оба venv автоматически.

Stagehand подключён к OpenAI-compatible endpoint локального Ollama `http://127.0.0.1:11434/v1`. Он получает инертный DOM-snapshot уже загруженной ARGUS публичной страницы: scripts, event handlers и внешние subrequests удаляются до `observe`. Он делает только один bounded `observe` и возвращает безопасные click selectors. Он не извлекает факты и не выполняет найденное действие сам.

Browser Use ограничен разрешёнными доменами, количеством шагов, временем, объёмом истории и размером результата. Файловые операции, загрузки и поисковый tool отключены. Запрещены login, регистрация, оплата, CAPTCHA, paywall, доступ к скрытым API и любые действия, изменяющие состояние внешней системы.

## Деградация и health

`GET /v1/health` и worker `/readyz` публикуют состояние LLM отдельно от PostgreSQL и worker queue. Если `ARGUS_LLM_REQUIRED=false`, недоступная Ollama переводит общий статус в `degraded`, но не делает API/worker неготовым и не останавливает детерминированный сбор. Timeout или некорректный JSON отдельного компонента также завершается его безопасным fallback.

При `ARGUS_LLM_REQUIRED=true` embedded/worker проверяет модель при старте; worker не берёт новые задания во время недоступности Ollama и автоматически продолжает после восстановления. Активный сбор не уничтожается.

## Инварианты данных

- LLM output не является Evidence.
- Exact excerpt обязан присутствовать в fetched source text.
- Поисковый сниппет и карта-страница поиска остаются navigation-only.
- Source contour описывает путь поиска, а не тип факта.
- Семь обязательных source contours и три публичные карты сохраняют независимые линии покрытия.
- Для каждой карты обрабатываются все улицы, вошедшие в радиус; отсутствие результата фиксируется в telemetry, а не скрывается.
- Recovery, checkpoints, lease fencing, provenance, Tool Packs и consumer-neutral module contract продолжают работать независимо от состояния LLM.
