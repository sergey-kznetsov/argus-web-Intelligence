# Historical timelines and change observations

ARGUS historical mode combines exact Wayback capture discovery with normal factual extraction. Historical conclusions are limited to deterministic comparisons between two evidence-backed captures of the same original URL.

## Capture ordering

Wayback CDX results are converted into archived-page tasks in ascending capture timestamp order. This gives the collection a deterministic `oldest -> newest` execution sequence regardless of provider response ordering.

Each archived-page task carries:

- original public URL;
- Wayback capture URL;
- 14-digit capture timestamp;
- archive provider and discovery rank.

## Archive factual boundary

Archived pages are parsed by the same Generic Web factual stack as live pages. Extracted Observations/Evidence receive archive provenance containing the original URL and capture timestamp.

ARGUS does not recursively follow ordinary links extracted from Wayback captures. Archived links are frequently rewritten by the archive and can cause uncontrolled historical crawling. Additional historical research is instead created by the bounded `HistoricalBranchPlanner` from source-declared factual entity labels.

Derived historical comparison Observations never seed new historical queries, preventing a feedback loop where ARGUS researches its own generated timeline rows.

## Recovery-safe comparison

The historical adapter compares a capture only against an earlier capture already committed in repository storage. It never uses process-local or uncommitted state as the previous version.

If a worker crashes after extraction but before atomic commit, a recovery worker will not treat that abandoned extraction as historical truth. The next comparison is derived only from committed Observation rows.

## Page versions

For each archived page ARGUS emits `historical_page_version`.

The first observed capture is explicitly classified as:

`first_observed_capture`

ARGUS does not call this an appearance event because no earlier capture has been observed.

When a committed previous capture exists, the page version is classified as either:

- `page_content_changed`; or
- `page_content_unchanged`.

A changed page records current/previous content hashes, capture timestamps, Observation IDs and a bounded unified text diff. The default diff limit is 20,000 characters.

## Entity changes

Structured entities are matched across adjacent committed captures using a stable source-declared `entity_id` when available. If no stable ID exists, ARGUS falls back to normalized entity type plus source-declared name/title.

Between two observed captures ARGUS can emit `historical_entity_change` with:

- `appeared_between_captures`;
- `disappeared_between_captures`;
- `fields_changed`.

The current deterministic field comparison covers:

- title;
- name;
- operator;
- brand;
- former_name;
- old_name.

Field changes preserve explicit `from` and `to` values. ARGUS does not infer why a change occurred, whether an operator legally changed, whether an entity was created/destroyed, or what happened outside the interval between the two observed captures.

## Evidence

Every derived page/entity change has its own `historical_comparison` Evidence item containing the exact bounded comparison facts and links back to the previous/current source Observation IDs.

Derived rows are marked:

- `derived_from_evidence=true`;
- `semantic_inference=false`.

They also pass through the common ARGUS provenance/evidence-quality layer before atomic persistence.

## Budgets

Historical entity comparison is bounded. The default maximum is 100 emitted entity changes per archived page transition. If more changes are observed, the source result becomes partial and ARGUS emits `HISTORICAL_CHANGE_BUDGET_EXHAUSTED` rather than silently dropping the fact that extraction was truncated.

## Consumer boundary

The historical layer produces source-backed timelines and diffs only. Kraken, Janus or another analytical consumer decides how those changes should be interpreted. ARGUS contains no consumer-specific historical branches.
