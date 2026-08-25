# ADR-0004 Addendum 005: Capability handshake integration

- Status: Accepted before engine behavior implementation
- Date: 2026-08-23
- Evidence class: `E1_ENGINEERING`
- Profile: `LICHESS_CRAZYHOUSE_2026_08_12`
- Profile SHA-256: `d0602bc32877639f2d9a70741614882512083431b48b9f4e98a88e1067eb4d68`

## Correction boundary

The earlier product contract in `g4-referee-cases-v1.addendum.001.json` remains authoritative. A later referee-only experiment split the profile ID and hash into two declarations, changed the acknowledgement spelling, and challenged engines before configured evaluator options were applied. That behavior was never propagated to the product and is superseded. Historical receipts remain immutable and retain only their original scope.

The production contract uses the exact combined `CrazyhouseProfile` token and adds exactly one string option, `CrazyhouseCapabilityNonce`, with an empty default. It does not add `CrazyhouseProfileHash`. The exact acknowledgement is:

`info string crazyhouse_capability_ack status=ok profile=LICHESS_CRAZYHOUSE_2026_08_12 profile_sha256=d0602bc32877639f2d9a70741614882512083431b48b9f4e98a88e1067eb4d68 nonce=<32 lowercase hexadecimal characters>`

## Transactional semantics

`setoption name CrazyhouseCapabilityNonce value ...` stages a single pending capability challenge and does not modify the search route. On the next `isready`, the existing transactional route must commit successfully first. Only an active Crazyhouse route bound to the configured legacy evaluator may emit the exact acknowledgement, immediately before `readyok`. The challenge is consumed after that acknowledgement, so later ordinary readiness barriers do not repeat it.

An invalid nonce emits the stable error `crazyhouse_capability_nonce_invalid` and withholds both acknowledgement and `readyok`. A failed evaluator or route emits the pre-existing typed route failure and cannot emit the capability acknowledgement. Standard chess without a pending challenge retains its existing readiness behavior.

The referee owns freshness and cross-engine separation by generating a new nonce for each engine and pre-game barrier. The engine proves that the acknowledged profile, route and evaluator were active for the received challenge; it does not generate the nonce.

## Frozen evidence

`tests/crazyhouse/capability-handshake-v1.json` and `tests/crazyhouse_capability_handshake.py` are frozen before product behavior changes. They require exact inventory, positive ordering, one-shot acknowledgement, invalid-nonce failure, missing-network failure and standard-chess isolation. A canary, match or strength result cannot substitute for this contract.

The exact match referee must run the same combined-token contract before any local or OpenBench game. A protocol-only adapter for an external comparator is admissible only if unmodified and adapted comparator searches are proven identical on a frozen fixed-node corpus.

## Gate boundary

This addendum changes only the G4 capability sub-boundary and the affected G3 UCI inventory/G5 evaluator route replays. It makes no strength, OpenBench, champion, release or publication claim. G4 remains in progress until the real candidate and referee complete an authenticated two-process pre-game join.
