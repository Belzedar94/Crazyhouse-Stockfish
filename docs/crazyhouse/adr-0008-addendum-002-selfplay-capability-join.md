# ADR 0008 addendum 002: Distinct self-play capability join

- Status: accepted before any self-play build or run
- Date: 2026-08-24
- Evidence class: `E1_ENGINEERING`
- Corrects: capability ambiguity in addendum 001

## Defect found during implementation review

The G9 capability contract proves the physical codec, source identity and crash-safe transaction used by frozen trajectory replay. It does not assert that the same artifact contains product search, authenticates the registered legacy network, accepts OpenBench stdin framing, or generates complete games. Reusing that response alone would permit a G9 replay-only binary to be mistaken for a P11 self-play producer.

No self-play build or result had been observed when this defect was found. The original G9 capability and every G9 receipt remain immutable.

## Additive decision

`tests/crazyhouse/datagen-selfplay-capability-v1.json` is the separate self-play admission contract. Its exact 4,563 LF-only bytes have SHA-256 `482fd210ed4009aaf145c34d44b18fc05f99b11969e69dd9f69d9907204c87dd`.

The new positive request is `--datagen-selfplay-capabilities-v1 --challenge <32-lowercase-hex>`. Its canonical response binds the same producer self-hash and clean source identity as G9 plus the exact self-play command, physical-record count unit, complete-trajectory rule, one-thread V1 ceiling, product search backend, exact-only bounds, registered network identity, score kinds, stdin framing and transaction.

Search-backed generation derives a fresh per-chunk challenge before opening output and embeds the new response digest in provenance and the chunk header. The older response cannot satisfy this join. Direct G9 trajectory replay continues to use the older response and must remain byte-identical.

This correction changes no rule, evaluator, search policy, canary input or expected output. Passing the new handshake remains only a local P11 precondition and grants no OpenBench or production authority.

