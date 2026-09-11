# Janus residential facts contract

ARGUS registers `janus.parking.potential.uds` as a bounded analytical consumer.

The supported capability is `residential_facts` version 1. This capability allows only `residential_premises_count`.

The factual source remains `dom.mingkh.ru` through the existing `mingkh_residential` acquisition path. Janus cannot select arbitrary ARGUS source packs through this contract and ARGUS does not execute any parking calculations.

Typical request fields:

- `consumer`: `janus.parking.potential.uds`;
- `consumer_profile_version`: `1`;
- `capability`: `residential_facts`;
- `requested_facts`: `["residential_premises_count"]`;
- `intents`: `["residential_premises_count"]`;
- building address and coordinates in `territory`.

If the public source requires an access challenge, ARGUS returns a partial/blocked result according to its normal evidence contract; Janus must not invent the missing premises count.
