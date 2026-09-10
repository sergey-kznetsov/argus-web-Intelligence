# Consumer profiles и Tool Packs ARGUS

## Зачем они нужны

ARGUS — один общий web-intelligence backend для разных аналитических модулей. Crawler/runtime, Evidence/Provenance, storage, queue, security и recovery остаются универсальными, но каждый product consumer получает версионируемый factual contract и Tool Pack.

```text
consumer module ID
  -> ConsumerProfileRegistry
  -> capability
  -> requested_facts
  -> ToolPackRegistry
  -> planner / extractor / source policy
  -> SourceRegistry
  -> FAST -> BROWSER
  -> Observation / Evidence / Provenance
```

AGENT в текущем service graph не подключён.

## CollectionRequest

Пример профилированного запроса:

```json
{
  "consumer": "kraken.development.uds",
  "consumer_profile_version": 1,
  "capability": "urban_signals",
  "requested_facts": [
    "complaint",
    "public_appeal",
    "post",
    "comment"
  ]
}
```

ARGUS разрешает и сохраняет server-owned execution contract, включая `tool_pack_id` и его version. Caller не может подменить pack другим значением.

`intents`, territory и constraints описывают задачу и операционные границы. `capability`/`requested_facts` определяют требуемые factual types. Tool Pack задаёт, какие planners/adapters/extractors допускаются для этого contract.

## Runtime isolation

Tool Pack активируется на время Collection через `contextvars`. `SourceRegistry` применяет его при выборе initial adapters, discovery-routed adapters и child tasks. Source ID вне активного pack отклоняется до fetch.

Это позволяет параллельным Collections работать с разными contracts без утечки consumer-specific tooling между ними.

## Текущие profiles

### Kraken

```text
consumer: kraken.development.uds
profile version: 1
capability: urban_signals
tool pack: kraken.urban_signals v1
```

Допустимые factual types:

```text
complaint
public_appeal
post
comment
resident_message
local_news_mention
incident_mention
```

`review` не входит в текущий Kraken contract. Reviews заведений не должны превращаться в Kraken messages. Публичные карты могут давать spatial/navigation context, но information-only map observations отфильтровываются на consumer delivery boundary; Evidence для такого контекста сохраняется.

Текущий Kraken Tool Pack допускает общие source adapters, необходимые `urban_signals`, включая:

```text
generic_web
rss_atom
json_feed
site_discovery
openstreetmap_overpass
```

Residential/historical adapters вроде `mingkh_residential` и `pastvu_historical` не принадлежат этому pack.

Для planner policy `urban_signals` включён отдельный MandatoryCoverage orchestrator: source contours и public-map providers проходят в фиксированном обязательном контуре, после чего включается bounded optional research. Mandatory pass не пропускается из-за seed coverage или recovery state.

### Test

```text
consumer: test
profile version: 1
capability: generic_research
```

Это internal CI/manual smoke profile, а не product consumer.

## Legacy requests

Незарегистрированный consumer временно может использовать старый `consumer + intents` contract, только если не передаёт profile-specific fields. Новый product module должен иметь зарегистрированный profile и Tool Pack.

## Добавление нового модуля

1. Зафиксировать stable `consumer_id`.
2. Описать capabilities и factual result types.
3. Создать versioned Tool Pack.
4. Определить allowed source adapters/shared tools.
5. Определить planner/extractor/recipe policies.
6. Зарегистрировать profile и pack декларативно.
7. Добавить необходимые adapters/extractors через общие extension contracts.
8. Добавить contract/isolation/E2E tests.
9. Развернуть конкретный CI-green ARGUS release.

Нельзя добавлять consumer-specific business control flow в Core:

```python
if consumer == "kraken":
    ...
```

Registries содержат routing/contract metadata, а аналитические выводы остаются внутри consumer module.

## Planned profiles

Janus и Historical должны получить отдельные profiles/packs только после фиксации их реальных input/factual contracts. Наличие соответствующих adapters в ARGUS само по себе не означает, что такой consumer profile уже зарегистрирован.
