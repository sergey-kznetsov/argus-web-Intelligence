# Development sequence

This file fixes the agreed implementation order for ARGUS and the Geo Analyzer
consumer modules.

## Principle

Do not attempt to finish a hypothetical universal ARGUS before real consumers
exist. Keep the ARGUS core universal and grow consumer-specific research
profiles from working module contracts.

ARGUS core responsibilities remain:

```text
find -> obtain -> prove -> store
```

Consumer module responsibilities remain:

```text
interpret -> calculate -> conclude
```

## Stage 1 — ARGUS contract foundation

Status: current stage.

Required before switching back to Kraken:

- stable module ID in `CollectionRequest.consumer`;
- `ConsumerProfileRegistry`;
- versioned `capability`;
- bounded `requested_facts`;
- compatibility path for old unregistered requests during migration;
- first Kraken profile skeleton;
- tests and contract documentation.

Do not finish Kraken-specific extraction here. The exact input shape must come
from the rebuilt Kraken pipeline.

## Stage 2 — rebuild Kraken as the first real consumer

Use the original SOIKA repository as the algorithmic reference:

`https://github.com/Mvin8/SOIKA.git`

Use Urbanomy/Urbanomy-data only as an additional spatial research reference,
not as a claimed direct SOIKA dependency.

Kraken must first work correctly on a prepared message dataset without ARGUS.

Target domain pipeline:

```text
Geo Analyzer territory context
    -> source messages
    -> preprocessing / NLP
    -> urbanonym and geospatial resolution
    -> territorial filtering
    -> events
    -> semantic/spatial connections
    -> activity / risk
    -> Geo Analyzer module result
```

At this stage define exactly:

1. what Kraken receives from Geo Analyzer;
2. what Kraken requests from ARGUS;
3. the minimum required fields of every source message;
4. what Kraken calculates itself;
5. what Kraken returns into the Geo Analyzer report.

## Stage 3 — Kraken <-> ARGUS end-to-end

After Kraken's input contract is stable:

- update the Kraken profile in ARGUS;
- add Kraken-oriented discovery priorities;
- add Kraken consumer-facing normalization;
- keep raw document/metadata/structured observations as internal
  Evidence/Provenance where useful;
- return message-like factual entities needed by Kraken;
- validate territory relevance and inherited page/entity provenance;
- add deduplication around factual identity, not URL alone;
- tune research sufficiency and time budgets.

TEST acceptance path:

```text
Geo Analyzer
    -> Kraken
    -> ARGUS
    -> public internet
    -> ARGUS factual messages + Evidence
    -> Kraken analysis
    -> Geo Analyzer report
```

Production is not touched until the TEST lifecycle passes.

## Stage 4 — Janus

Only after the Kraken vertical works:

- finalize the real Janus input contract;
- register the Janus module ID and profile;
- implement its requested building/demographic facts in ARGUS;
- integrate and run Janus <-> ARGUS <-> Geo Analyzer E2E.

Do not reuse Kraken extraction semantics for Janus.

## Stage 5 — Historical

Then:

- finalize Historical inputs;
- register its profile;
- connect archive/time-oriented ARGUS capabilities;
- run Historical <-> ARGUS <-> Geo Analyzer E2E.

## Stage 6 — future consumers

Add new capabilities only from a real consumer requirement. Examples include
competitive/developer-site intelligence.

A new consumer should normally require:

```text
profile registration
+ capability/fact contract
+ only the extractors/SiteRecipes actually needed
+ E2E acceptance
```

It should not require a fork or copy of ARGUS.

## Test/deployment rule

For every consumer integration:

```text
unit/contract tests
-> ARGUS CI
-> module CI
-> Geo Analyzer TEST install
-> health
-> analysis
-> reinstall
-> analysis
-> delete/reinstall lifecycle where applicable
-> only then production
```
