# Provenance и качество Evidence

ARGUS добавляет единый provenance envelope к каждому Observation непосредственно перед atomic commit source task. В той же транзакции обогащается metadata связанного Evidence.

Этот слой описывает, **как** факт получен. Он не решает, истинно ли утверждение источника, и не создаёт произвольный confidence score.

## Observation provenance

`Observation.provenance.argus` содержит bounded versioned envelope:

- source adapter ID и source kind;
- source URL;
- collection, analysis и consumer identifiers;
- collection timestamp;
- Observation content hash;
- research goals/intents;
- runtime получения;
- extractor version;
- Snapshot ID;
- bounded Snapshot metadata, если Snapshot входит в текущий atomic commit;
- bounded discovery/navigation metadata, если URL пришёл из discovery.

Текущая version: `argus-provenance/1`.

Observation может ссылаться на Snapshot, который существовал до текущего task. Тогда `snapshot_id` сохраняется, но payload Snapshot повторно не дублируется.

## Evidence provenance

Каждый Evidence, связанный с Observation, получает `metadata.argus_provenance`:

- Observation ID/content hash;
- source adapter/kind/URL;
- Evidence collection timestamp;
- Snapshot ID;
- extractor/runtime;
- research goals;
- bounded discovery metadata;
- SHA-256 exact Evidence text;
- `truth_confidence_assigned=false`.

Evidence text hash идентифицирует сохранённый excerpt независимо от document content hash.

## Evidence quality

`Observation.quality.evidence_quality` — техническая запись качества, а не truth score. Она показывает:

- есть ли linked Evidence;
- число Evidence items;
- указан ли Snapshot;
- присутствует ли этот Snapshot в current task commit;
- есть ли Observation content hash;
- является ли representation machine-readable;
- partial/truncated extraction;
- exact duplicate state;
- совпадают ли Evidence source URLs с Observation URL;
- `truth_confidence_assigned=false`.

Текущая version: `evidence-quality/1`.

ARGUS намеренно не создаёт synthetic numeric confidence. Consumer может применять собственную evidence-weighting policy по явным source/evidence properties.

## Atomicity

Normalization provenance/quality происходит после source extraction/normalization и непосредственно перед `commit_task_success`. Observation, Evidence, Snapshot и checkpoint публикуются одной транзакцией.

При failure commit или lease loss частично enriched rows не публикуются; recovery повторяет task от последнего durable checkpoint.

## Discovery boundary

Discovery metadata может присутствовать в provenance только как объяснение navigation path. Discovery results не становятся Evidence.

## Consumer boundary

Consumer identity сохраняется для traceability, но provenance logic универсальна. Kraken/Janus/будущие modules получают один и тот же evidence contract.
