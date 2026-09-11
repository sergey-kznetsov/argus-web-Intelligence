# Change record: Janus residential consumer

Date: 2026-09-11

ARGUS adds one bounded consumer profile for `janus.parking.potential.uds`.

The consumer may request only `residential_premises_count` through capability `residential_facts`. The tool pack allows the existing `mingkh_residential` source path plus its navigation helpers. Parking calculations remain outside ARGUS.

Verification: consumer-contract regression tests, existing residential-source tests, full CI, then TEST integration with Janus.
