# Source-declared metadata HTML-страниц

ARGUS извлекает bounded набор machine-readable metadata, явно объявленных публичной HTML-страницей. Этот layer дополняет visible text и JSON-LD, но не заменяет их.

Текущий vocabulary ограничен распространёнными стандартами:

- Open Graph core properties;
- Open Graph `article:*`;
- Dublin Core / DCMI title, creator, date, description;
- HTML `rel=canonical`;
- обычные HTML description/author metadata.

Дата, автор или canonical identity не угадываются из body text, URL или CSS classes.

## Extraction boundary

Extractor локальный и не выполняет сетевых запросов. Он сканирует только bounded начало HTML (внутренний hard limit 500 000 chars по умолчанию), ограничивает individual values и repeated arrays.

Сохраняются только HTTP(S) canonical/`og:url` без URL userinfo. Такие URLs не fetch'ятся самим extractor. Если позже URL становится crawl task, он проходит обычный SSRF/redirect guard.

При конфликте singleton Open Graph properties используется первое объявленное source-order value. Повторные `article:author`/`article:tag` сохраняются bounded arrays.

## Семантика дат

ARGUS различает source declaration и interpretation.

`article:published_time` имеет явную publication semantics и может заполнить `Observation.published_at`, если значение корректно разбирается как ISO-style datetime.

Dublin Core `date` шире publication time, поэтому `dc_date`/`dcterms_date` сохраняется как объявлено, но не автоматически становится `published_at`. `dcterms.created` аналогично остаётся source-declared creation metadata.

## Observation и Evidence

При наличии хотя бы одного поддерживаемого поля Generic Web создаёт дополнительный Observation:

- `source_kind=page_metadata`;
- `entity_type=document_metadata`;
- URL = реально fetched final URL;
- entity identity может использовать безопасно объявленный canonical URL;
- data = normalized source fields;
- provenance связан с тем же raw page Snapshot;
- quality содержит `evidence_backed`, `machine_readable`, `source_declared`.

Отдельный `page_metadata` Evidence хранит canonical JSON extracted fields и source URL фактически fetched страницы. Canonical declaration не заменяет proof location.

Страницы без поддерживаемой metadata продолжают давать обычный Generic Web Observation/Evidence.

## Scope

Layer полезен для local media, official sites, public portals и страниц со стабильной machine-readable metadata.

Он не:

- выводит article status из layout;
- считает любую дату publication date;
- ранжирует domain по metadata;
- дедуплицирует URL по canonical declaration сам по себе;
- выполняет remote metadata contexts;
- делает consumer-specific выводы.
