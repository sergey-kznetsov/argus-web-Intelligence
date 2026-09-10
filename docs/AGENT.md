# AGENT: контракт и текущее состояние

AGENT — подготовленный слой навигации для случаев, когда детерминированные FAST/BROWSER механизмы не могут получить нужное публичное представление. AGENT не является factual source, а сгенерированный моделью текст никогда не может становиться Evidence.

## Текущее состояние

В актуальном production service graph AGENT **не подключён**. `build_services()` создаёт `AtomicContentWebAdapter` с `agent=None`, а `llm_health=None`. В `Settings` значения по умолчанию — `agent_backend="disabled"`, `agent_enabled=false`; Ollama-параметры сохранены для совместимости старых env-файлов.

Поэтому фактическая рабочая цепочка сейчас:

```text
verified SiteRecipe replay, если уже существует
-> FAST
-> BROWSER при необходимости
-> factual extraction
```

Код `OllamaRecipeAgent`, Browser Use/Stagehand boundaries, `AgentRecipeCompiler` и семантические AGENT-механизмы остаётся в репозитории, но это dormant/experimental infrastructure. Документация и acceptance не должны выдавать её за реально выполняемый production fallback.

## Правило возможного повторного включения

Если AGENT снова подключается, сохраняются следующие обязательные границы:

- модель выбирает только bounded набор действий/контролов, сформированный ARGUS;
- произвольные selectors, произвольный JavaScript, filesystem actions и произвольные URL не становятся исполняемыми напрямую;
- navigation path компилируется в deterministic `SiteRecipe`;
- кандидат должен успешно пройти BROWSER replay;
- только после успешного non-blocked replay возможна активация recipe;
- factual extraction выполняется с реально полученной страницы, а не с ответа модели;
- CAPTCHA, login, access-control, paywall и rate-limit challenges не обходятся;
- domain/URL boundary и `UrlGuard` продолжают действовать;
- все step/action/time/size бюджеты остаются bounded.

## SiteRecipe

Существующая инфраструктура SiteRecipe продолжает быть полезна независимо от того, включён ли AGENT: ранее сохранённый активный deterministic recipe может быть replayed BROWSER runtime. Создание новых agent-generated recipes в текущем service graph не происходит, потому что AGENT туда не передан.

## Evidence boundary

Navigation telemetry, recipe metadata и возможный AGENT output объясняют способ доступа к странице, но не являются доказательством предметного факта. Fact должен быть извлечён из fetched source и иметь Observation/Evidence/Provenance.
