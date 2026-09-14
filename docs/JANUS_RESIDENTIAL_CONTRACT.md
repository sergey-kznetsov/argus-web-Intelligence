# Janus residential facts contract

ARGUS registers `janus.parking.potential.uds` as a dedicated consumer-specific acquisition contour. It is independent from the Kraken/SOIKA contour and does not share the Kraken/SOIKA source pool or research workflow.

The supported capability is `residential_facts` version 1. This capability allows only `residential_premises_count`.

## Source boundary

The Janus contour has exactly one factual source adapter: `mingkh_residential`.

Its network/domain boundary is exactly `dom.mingkh.ru`. The Janus contour does not use `site_discovery`, `generic_web`, public-map acquisition, search-provider fallback, Kraken/SOIKA sources, or arbitrary external seed URLs. Address lookup and house-page navigation are performed inside the public `dom.mingkh.ru` interface.

A normal Janus request is clamped to one source task and zero recursive depth. This prevents adaptive web research from widening the source pool after the dedicated source attempt.

## Responsibility boundary

ARGUS is an acquisition backend for this capability. It may navigate the source interface, handle the existing human-in-the-loop access-challenge flow, parse the page, validate source/address relevance, preserve Evidence/Provenance, and return the source-declared residential-premises count.

ARGUS does not classify parking, decide whether parking is public or restricted, aggregate parking supply, calculate a coefficient, calculate parking potential, assign a score/band, or produce a Janus business conclusion. Those operations belong to Janus.

The returned fact is accepted only when `dom.mingkh.ru` explicitly exposes the requested value for the requested building. Missing, conflicting, blocked or unverifiable values remain missing; ARGUS never estimates or fabricates them.

## Request

Typical request fields:

- `consumer`: `janus.parking.potential.uds`;
- `consumer_profile_version`: `1`;
- `capability`: `residential_facts`;
- `requested_facts`: `["residential_premises_count"]`;
- `intents`: `["residential_premises_count"]`;
- building address and coordinates in `territory`;
- `allowed_domains`: resolved to `["dom.mingkh.ru"]`;
- effective `max_pages`: `1`;
- effective `max_depth`: `0`.

If `dom.mingkh.ru` requires a CAPTCHA or other user verification, ARGUS keeps the challenge inside this source contour and exposes the existing bounded human-interaction flow. Until the source is successfully verified, the factual count is not produced. If verification is not completed, ARGUS returns blocked/partial evidence state and Janus must not invent the missing premises count.

Kraken/SOIKA is not involved in this contract and its acquisition/delivery pipeline must remain unchanged.
