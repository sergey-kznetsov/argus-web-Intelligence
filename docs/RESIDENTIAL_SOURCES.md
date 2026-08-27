# Residential building facts

ARGUS treats residential building counts as source-scoped factual intents rather than generic semantic web questions.

Current intents:

- `residential_population` — source-declared resident count;
- `residential_premises_count` — source-declared apartment/residential-premises count.

## Mandatory source

The current factual source is the public web interface of `dom.mingkh.ru`, represented by SourceAdapter `mingkh_residential`.

This is an intent-to-source policy, not a consumer-specific branch. ARGUS does not check whether the caller is Janus, Kraken or another module. Any consumer requesting these intents receives the same source contract.

For a residential-only collection, discovery is fail-closed to `dom.mingkh.ru`. Search providers may be used to locate the corresponding public house page, but search results are navigation only and never Evidence. ARGUS does not fall back to unrelated housing sites for these two intents.

Mixed collections preserve normal ARGUS research for their other intents. Generic semantic classification is explicitly prevented from proving the source-scoped residential intents, so an alternative web page cannot accidentally satisfy their factual coverage.

## Evidence contract

A residential fact is emitted only when all of the following are true:

1. the final fetched URL remains inside `dom.mingkh.ru`;
2. the page passes deterministic territory/address relevance for the requested house;
3. the value is explicitly published next to a recognized source label;
4. the value is an unambiguous non-negative integer;
5. the source snapshot and exact label/value Evidence are preserved.

Supported premises labels currently include:

- `Количество квартир`;
- `Количество жилых помещений`;
- `Жилых помещений`.

Supported population labels currently include:

- `Количество жителей`;
- `Численность жителей`;
- `Число жителей`.

If multiple different values are exposed for one intent on the same page, ARGUS returns `MINGKH_RESIDENTIAL_VALUE_CONFLICT` instead of choosing one.

## No population inference

ARGUS never derives resident count from apartment count, residential area, average household size or an LLM estimate. If the public source declares the number of apartments but does not declare resident count, only `residential_premises_count` is evidenced and `residential_population` remains uncovered.

This is consistent with the global ARGUS evidence-first rule: a missing fact is preferable to a fabricated or model-derived value.

## Access challenges

CAPTCHA and anti-bot verification are access-control boundaries, not research tasks. The adapter recognizes both runtime blocking and common English/Russian challenge text, including the current class of `не робот` / `решите пример` pages.

When a challenge is present, ARGUS returns `MINGKH_ACCESS_CHALLENGE` with `blocked=true`. It does not solve the challenge, submit an answer, use an LLM to bypass it, or silently replace the mandatory source with a different factual provider.

This policy does not prevent normal BROWSER/AGENT navigation when the public site is accessible without an access challenge. The same FAST → BROWSER → AGENT infrastructure and SiteRecipe system remain available for legitimate UI interaction.

## Provenance

Successful values are stored as `residential_building_fact` Observation/Evidence pairs. The factual payload includes:

- intent;
- integer value;
- source label;
- `estimated=false`.

Provenance includes the temporal Snapshot id, extractor version and territory-relevance basis. Observation/Evidence ids are deterministic within the collection, preserving replay safety after worker recovery.
