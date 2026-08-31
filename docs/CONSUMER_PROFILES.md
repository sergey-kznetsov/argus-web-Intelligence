# ARGUS consumer profiles

## Purpose

ARGUS is one web-intelligence backend shared by multiple analytical modules. The
crawler/runtime layer remains universal, while each product consumer declares a
versioned research contract.

The contract answers three questions before research starts:

1. which module is calling ARGUS;
2. which capability of that module is being requested;
3. which factual result types the module wants back.

This prevents ARGUS from treating Kraken, Janus, Historical and future consumers
as one generic `public_mentions` parser.

## CollectionRequest fields

Profiled consumers use the existing `consumer` field as their stable module ID
and add three fields:

```json
{
  "consumer": "kraken.development.uds",
  "consumer_profile_version": 1,
  "capability": "urban_signals",
  "requested_facts": [
    "review",
    "complaint",
    "post",
    "comment"
  ]
}
```

`intents`, territory and constraints remain part of protocol `1.0.0`. They still
describe research goals and operational limits. `capability` and
`requested_facts` define the consumer-specific result contract.

Known profiles are resolved when `CollectionRequest` is validated. A known
consumer receives its default capability and default requested facts when those
fields are omitted. Unsupported capabilities, facts or profile versions are
rejected before the collection enters the queue.

## Migration rule

Legacy consumers that send only the old fields are temporarily accepted even if
they are not registered. This compatibility path is intentionally narrow:

- an unregistered consumer may use the old `consumer + intents` contract;
- an unregistered consumer may not send `capability`,
  `requested_facts` or `consumer_profile_version`;
- once a module is migrated, it must use its registered stable module ID.

This keeps existing operational and test clients working while product modules
move to explicit contracts one by one.

## Initial Kraken profile

The first product profile is:

```text
consumer: kraken.development.uds
profile version: 1
default capability: urban_signals
```

Allowed factual types:

```text
review
complaint
public_appeal
post
comment
resident_message
local_news_mention
incident_mention
```

These are input facts for Kraken. ARGUS still owns discovery, acquisition,
Evidence/Provenance, snapshots and storage. Kraken owns interpretation,
classification, geospatial-semantic analysis, event formation, activity/risk
calculation and the final Geo Analyzer report section.

The exact Kraken result schema will be finalized from the rebuilt Kraken
pipeline before ARGUS gets Kraken-specific normalization/extraction rules.

## Next consumers

Janus and Historical are deliberately not frozen in the registry yet. Their
profiles must be added only after each module's real input contract is finalized.

Expected direction:

```text
Janus
  -> building demographics
  -> apartments / residential units / residents / management data

Historical
  -> territory history
  -> archived pages / photos / past organizations / events / timeline
```

Competitive intelligence should follow the same mechanism as a future
registered consumer/capability rather than becoming hard-coded branching inside
ARGUS.

## Design rule

Do not implement consumer behavior as:

```python
if consumer == "kraken":
    ...
elif consumer == "janus":
    ...
```

The stable structure is:

```text
consumer module ID
    -> ConsumerProfileRegistry
    -> capability
    -> requested facts
    -> Research Planner / discovery priorities
    -> source adapters / SiteRecipe
    -> FAST -> BROWSER -> AGENT
    -> extraction
    -> Evidence / Provenance
    -> consumer-specific normalization
```

The registry is contract metadata. It must not contain scraping logic or
site-specific selectors.
