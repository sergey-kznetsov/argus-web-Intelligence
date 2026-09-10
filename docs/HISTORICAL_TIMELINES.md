# Исторические временные линии и изменения

Historical layer ARGUS сочетает exact Wayback capture discovery с обычным factual extraction. Derived historical conclusions ограничены deterministic comparison двух evidence-backed captures одного original URL.

## Порядок captures

Wayback CDX results преобразуются в archived-page tasks по возрастанию capture timestamp. Collection получает deterministic порядок `oldest -> newest` независимо от исходной сортировки provider.

Каждый task содержит:

- original public URL;
- Wayback capture URL;
- 14-digit capture timestamp;
- archive provider и discovery rank.

## Archive factual boundary

Archived page разбирается тем же Generic Web factual stack, что и live page. Observation/Evidence получают archive provenance с original URL и capture timestamp.

ARGUS не следует обычным links из Wayback captures рекурсивно: archive часто переписывает ссылки, что может создать неконтролируемый crawl. Дополнительный historical research создаёт bounded `HistoricalBranchPlanner` из source-declared entity labels.

Derived comparison Observations не порождают новые historical queries.

## Recovery-safe comparison

Capture сравнивается только с более ранним capture, уже committed в Repository. Process-local/uncommitted state не используется как previous truth.

Если worker падает до atomic commit, abandoned extraction не участвует в следующем comparison.

## Page versions

Для каждого archived page создаётся `historical_page_version`.

Первый observed capture имеет классификацию:

```text
first_observed_capture
```

Это не называется appearance event, потому что более ранний capture не наблюдался.

При наличии committed previous capture используется:

```text
page_content_changed
page_content_unchanged
```

Changed page сохраняет current/previous hashes, timestamps, Observation IDs и bounded unified text diff. Default diff limit — 20 000 chars.

## Entity changes

Structured entities между соседними committed captures связываются по stable source-declared `entity_id`, а при его отсутствии — по normalized entity type + source-declared name/title.

`historical_entity_change` может иметь:

```text
appeared_between_captures
disappeared_between_captures
fields_changed
```

Текущий field comparison:

```text
title
name
operator
brand
former_name
old_name
```

Field changes сохраняют explicit `from`/`to`. ARGUS не выводит причину изменения, юридическую смену operator, создание/уничтожение entity или события вне интервала между observed captures.

## Evidence

Каждый derived change получает `historical_comparison` Evidence с bounded comparison facts и links на previous/current source Observation IDs.

Derived rows отмечаются:

```text
derived_from_evidence=true
semantic_inference=false
```

После этого применяется общий provenance/evidence-quality layer.

## Budgets

Default max — 100 emitted entity changes на один переход archived page. При превышении source result становится partial и возвращается `HISTORICAL_CHANGE_BUDGET_EXHAUSTED`.

## Consumer boundary

Historical layer создаёт source-backed timelines/diffs. Consumer module решает, как их интерпретировать. ARGUS не содержит consumer-specific historical analytics.
