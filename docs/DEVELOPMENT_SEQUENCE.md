# Порядок развития ARGUS и consumer-модулей

## Принцип

Не нужно заранее строить абстрактный «универсальный ARGUS на все случаи». Универсальное ядро расширяется от реальных contracts модулей.

```text
ARGUS  = find -> obtain -> prove -> store
Module = interpret -> calculate -> conclude
```

## Этап 1 — базовый consumer contract

Статус: реализован в текущем репозитории.

Есть:

- stable `consumer`;
- `ConsumerProfileRegistry`;
- versioned capability/profile contract;
- bounded `requested_facts`;
- legacy compatibility для незарегистрированных старых requests;
- Kraken profile v1;
- Tool Pack routing/isolation;
- contract tests.

## Этап 2 — Kraken как первый реальный consumer

Статус: активная разработка и интеграционная доводка.

Алгоритмический reference Kraken — SOIKA: `https://github.com/Mvin8/SOIKA.git`. ARGUS не переносит в себя NLP/events/risk Kraken.

```text
Geo Analyzer territory
  -> Kraken
  -> ARGUS factual messages/signals
  -> Kraken preprocessing / NLP
  -> urbanonyms / geospatial resolution
  -> territorial filtering
  -> events / connections
  -> activity / risk
  -> Geo Analyzer Module Result
```

На стороне ARGUS уже существует `kraken.development.uds` / `urban_signals`, mandatory research lanes, street-radius scope, `research_lane_coverage` и consumer delivery filtering. Это не заменяет реальный E2E через установленный Kraken.

## Этап 3 — Kraken <-> ARGUS E2E

Нужно считать завершённым только после реального цикла:

```text
Geo Analyzer TEST
  -> Kraken
  -> ARGUS
  -> public sources
  -> Observation/Evidence/Provenance/Coverage
  -> Kraken analytics
  -> Geo Analyzer report/export
```

При доводке:

- не выдавать page metadata/технические сущности за сообщения людей;
- сохранять territory relevance и source provenance;
- дедуплицировать factual identity, а не только URL;
- проверять полноту mandatory research lanes;
- ограничивать время/pages после обязательного контура;
- проверять install/reinstall/restart E2E.

## Этап 4 — Janus

После стабильного Kraken vertical:

- зафиксировать реальный Janus factual contract;
- зарегистрировать Janus consumer profile;
- выделить отдельный Tool Pack;
- подключить residential facts, включая residents и residential premises;
- проверить Janus -> ARGUS -> Geo Analyzer E2E.

Kraken semantics нельзя переиспользовать как Janus semantics.

## Этап 5 — Historical

После фиксации реального Historical module contract:

- зарегистрировать profile/tool pack;
- подключить archive/time-oriented capabilities;
- проверить Historical -> ARGUS -> Geo Analyzer E2E.

## Этап 6 — будущие consumers

Новая capability появляется из реальной задачи модуля. Обычно нужны:

```text
profile registration
+ factual contract
+ минимально необходимые adapters/extractors/SiteRecipes
+ E2E acceptance
```

Fork ARGUS под каждый модуль не допускается.

## TEST -> PROD

Любая consumer-интеграция проходит:

```text
unit/contract tests
-> ARGUS CI
-> module CI
-> Geo Analyzer TEST
-> install/health/analysis/result
-> restart/reinstall/analysis
-> только затем production
```
