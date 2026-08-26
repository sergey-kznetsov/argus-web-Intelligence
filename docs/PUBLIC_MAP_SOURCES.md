# Public map web sources

ARGUS treats Yandex Maps, 2GIS and Google Maps as public web sources, not as mandatory paid API providers.

The public-map path follows the same factual contract as the rest of ARGUS:

`discovery -> FAST/BROWSER -> deterministic public view -> optional AGENT -> verified BROWSER replay -> Observation/Evidence`

No map page, URL or AGENT action is itself evidence that a review, complaint, comment or discussion exists. Facts are accepted only after normal extraction from content fetched by ARGUS.

## Supported public surfaces

| Provider | Source ID | Public card detection | Deterministic review view |
| --- | --- | --- | --- |
| Yandex Maps | `yandex_maps_web` | `yandex.ru/maps/...` | organization cards with a numeric organization id are normalized to `/reviews/` |
| 2GIS | `2gis_web` | `2gis.ru/...` | firm cards with a numeric firm id are normalized to `/tab/reviews` |
| Google Maps | `google_maps_web` | `google.com/maps/...` or `maps.google.com` | not guessed from an arbitrary place URL |

The deterministic rewrites are navigation hints only. Every generated URL still passes the normal `UrlGuard`, browser limits and blocking detection before use.

Google Maps does not receive a guessed review URL. A review-specific Google URI requires additional place identity that ARGUS may not possess in the free public-web contour. When the requested facts are hidden behind a public UI control, ARGUS may use the bounded AGENT path and must verify its deterministic replay before extracting facts.

## Semantic goal satisfaction

Public map semantic escalation currently applies to:

- `reviews`;
- `comments`;
- `discussions`;
- `complaints`.

`incidents` remains a generic web-research intent. ARGUS may find incident evidence on any public source, including a map page when naturally discovered, but it does not spend curated public-map budget solely because `incidents` was requested.

A public map goal can be satisfied by either:

1. a source-declared structured entity such as `Review`; or
2. exact-excerpt semantic evidence produced by the generic web semantic classifier.

For exact-excerpt evidence, model output is never factual input. The excerpt must occur verbatim in the already fetched source text. `complaints` additionally requires deterministic complaint/problem markers. This keeps navigation/planning assistance separate from factual provenance.

## Escalation order

When a public card is fetched but the requested map-specific semantic facts remain uncovered:

1. ARGUS checks whether the provider exposes a deterministic public review view.
2. Yandex/2GIS review views are opened with the normal bounded BROWSER runtime.
3. The resulting content is extracted and evaluated with the same intent coverage evaluator used elsewhere.
4. If factual coverage improves, that result becomes the accepted source result.
5. If the deterministic review view is blocked, ARGUS records the block and does not invoke AGENT to route around it.
6. If it is not blocked but still does not expose the requested facts, ARGUS may invoke the configured AGENT once.
7. AGENT output is never Evidence. Any action path must be converted to a deterministic `SiteRecipe` and successfully replayed by BROWSER before content is extracted.

This preserves the project rule that CAPTCHA, access controls and anti-bot challenges are boundaries, not obstacles to bypass.

## Provenance

Observations/Evidence from recognized map pages receive `public_map_source` metadata based on host/path identity only.

The selected result also records:

- `public_map_review_view`: whether a deterministic review view was attempted, accepted or blocked and which public URL was used;
- `public_map_semantic_escalation`: semantic goals, AGENT attempt/acceptance state and any suppression reason;
- `agent_output_is_evidence = false` for the AGENT path.

These fields explain how ARGUS reached the source. They do not raise source confidence by themselves.

## Free-contour boundary

ARGUS does not require Yandex Maps API, 2GIS API or Google Places API for this path. Search engines and public map pages are discovery/navigation surfaces only. Provider-internal/private endpoints must not be treated as a stable contract merely because they are observable in browser traffic.

If a provider changes its public interface, ARGUS should prefer a new verified public navigation path or a repaired `SiteRecipe`, not a hidden endpoint copied from the site's frontend.
