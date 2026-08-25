# Provenance and evidence quality

ARGUS attaches a uniform provenance envelope to every Observation immediately before the source task is atomically committed. The same commit also enriches linked Evidence metadata.

This layer describes how a fact was obtained. It does not decide whether the fact is semantically true and does not assign an arbitrary confidence score.

## Observation provenance

`Observation.provenance.argus` contains a bounded, versioned envelope with:

- source adapter ID and source kind;
- source URL;
- collection, analysis and consumer identifiers;
- collection timestamp;
- Observation content hash;
- research goals/intents when present;
- runtime used to obtain the source;
- extractor version when available;
- Snapshot ID;
- bounded Snapshot metadata when the Snapshot is part of the current atomic task commit;
- bounded discovery/navigation metadata when the URL came from discovery.

The current envelope version is `argus-provenance/1`.

An Observation may reference a Snapshot that already existed before the current task. In that case `snapshot_id` remains present, while the current commit does not duplicate the Snapshot payload.

## Evidence provenance

Every Evidence item linked to an Observation receives `metadata.argus_provenance` with:

- Observation ID and Observation content hash;
- source adapter/source kind/source URL;
- Evidence collection timestamp;
- Snapshot ID;
- extractor/runtime information;
- research goals;
- bounded discovery metadata;
- SHA-256 of the exact Evidence text stored by ARGUS;
- `truth_confidence_assigned=false`.

The Evidence text hash protects the identity of the stored excerpt independently of the larger source-document content hash.

## Evidence quality facts

`Observation.quality.evidence_quality` is a technical quality record, not a truth score. It reports:

- whether linked Evidence exists;
- linked Evidence count;
- whether a Snapshot is referenced;
- whether that Snapshot is present in the current task commit;
- whether an Observation content hash exists;
- whether the representation is machine-readable;
- whether extraction is partial/truncated;
- whether the document is known duplicate content;
- whether linked Evidence source URLs match the Observation URL;
- `truth_confidence_assigned=false`.

The current quality version is `evidence-quality/1`.

ARGUS intentionally does not expose a synthetic numeric confidence score at this layer. A downstream analytical consumer may apply its own evidence-weighting policy, but it must do so from explicit source/evidence properties rather than treating an ARGUS crawler heuristic as factual confidence.

## Atomicity

Normalization happens after source extraction/normalization and immediately before `commit_task_success`. Provenance/quality changes are therefore persisted in the same transaction as Observation, Evidence, Snapshot and collection checkpoint state.

If the atomic task commit fails or the worker loses its lease, the enriched rows are not partially published. Recovery re-runs the task from the last committed checkpoint.

## Discovery boundary

Discovery metadata may appear inside provenance only to explain how ARGUS reached a source. Discovery results remain navigation and are explicitly not Evidence.

## Consumer boundary

The envelope contains the calling consumer identity for traceability, but ARGUS does not branch provenance logic by Kraken, Janus or any future consumer. All consumers receive the same source/evidence contract.
