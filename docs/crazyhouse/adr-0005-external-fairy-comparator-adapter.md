# ADR-0005: Fail-closed external Fairy comparator adapter

- Status: Accepted before adapter implementation
- Date: 2026-08-23
- Evidence class: `E1_ENGINEERING`
- Product source impact: none
- Strength claim: none

## Context

The owner requires a local same-network strength gate against current Fairy-Stockfish before any OpenBench submission. Fairy-Stockfish remains an external comparator only; it is not product ancestry. The exact source comparator at commit `6d9d0f5724677dc3aba3c577b0b482b6ec11e44a` built cleanly and used the approved legacy network during a real search. Its missing-network failure on the search worker nevertheless emitted fatal text and then remained alive. That raw executable is therefore not match-admissible.

The current referee also requires the frozen Crazyhouse profile and nonce handshake. Changing the comparator source would create a new comparator and would invalidate the requested raw-current-Fairy comparison. A protocol-only supervisor is admissible under ADR-0004 addendum 005 only after it proves fixed-node search transparency.

## Decision

Implement a standalone native Windows UCI supervisor under `tools/comparator_adapter/`. It is support tooling, not part of `src/`, and it may launch only the pinned Fairy executable. The adapter authenticates the child executable and legacy network before process creation, owns the exact child in a kill-on-close Windows Job Object, holds the authenticated network against write or delete for the child lifetime, and never restarts a child.

The adapter narrows the raw UCI surface to the Crazyhouse comparison contract. It replaces only the raw `UCI_Variant` declaration, injects `CrazyhouseProfile`, `CrazyhouseCapabilityNonce`, and `CrazyhouseEvalFile`, and intercepts those options plus raw `Use NNUE` and `EvalFile`. All other input and output traffic is relayed unchanged. `Use NNUE=false`, a non-Crazyhouse advertised variant, a wrong profile, an invalid nonce, an unauthenticated network request, child stderr, a fatal or classical marker, a bestmove before network proof, and an unexpected child exit are terminal failures.

Before exposing `uciok`, the adapter configures the raw child for Crazyhouse, `Use NNUE=true`, and the approved network. It then performs an internal main-thread `eval` followed by `isready`. All internal-probe output is suppressed. Exactly one line matching the full approved NNUE path is required; the classical marker and every `info string ERROR:` line are forbidden. This avoids relying on the rejected search-worker fatal path and proves the evaluator route before match admission.

Every real non-perft search is output-gated until the same exact NNUE marker is observed. `go perft` is a rules-only exception because the pinned Fairy source returns before evaluator verification. On each successful Crazyhouse readiness barrier, the adapter emits one honest external-comparator route record, then the nonce acknowledgement when a valid challenge is pending, then `readyok`. It never claims the product evaluator implementation.

The exact route record is:

`info string route_commit status=ok ruleset=crazyhouse profile=LICHESS_CRAZYHOUSE_2026_08_12 profile_sha256=d0602bc32877639f2d9a70741614882512083431b48b9f4e98a88e1067eb4d68 epoch=<positive decimal> backend=fairy-external identity=8ebf84784ad20fa33df403e60211818a7486db7cb8c3decfc86a80238d254f43 evaluator=halfkav2variants`

The capability acknowledgement remains byte-for-byte the production contract:

`info string crazyhouse_capability_ack status=ok profile=LICHESS_CRAZYHOUSE_2026_08_12 profile_sha256=d0602bc32877639f2d9a70741614882512083431b48b9f4e98a88e1067eb4d68 nonce=<32 lowercase hexadecimal characters>`

## Transparency and timing boundary

The frozen corpus in `tests/crazyhouse/p6-fairy-comparator-adapter-v1.json` runs raw and adapted modes in fresh processes with one thread, 16 MiB hash, cleared hash, identical positions, identical legacy bytes, and fixed node limits. After removing the explicitly injected adapter records and nondeterministic `time`, `nps`, and `hashfull` fields, every ordered search-information record, score, depth, node count, PV, bestmove, and ponder move must match. Any mismatch rejects the adapter.

This proof is functional, not a speed result. Before an equal-time strength panel, native relay overhead must be measured in repeated paired blocks on an otherwise timing-clean host. The adapter is not permitted to alter clocks, node limits, moves, positions, search output, or adjudication.

## Failure and lifecycle semantics

Every adapter rejection prints one stable `info string ERROR` record, withholds any pending acknowledgement and `readyok`, terminates only its owned Job Object, and exits nonzero. Normal `quit` is forwarded and must produce child exit zero, adapter exit zero, and no surviving descendant. EOF is treated as shutdown, not permission to leave a child alive. No broad process-name operation is permitted.

The missing, corrupt, incompatible, and byte-identical wrong-basename controls are distinct admission cases. The approved basename is `crazyhouse_run15rl_e190_l03.nnue`; bytes and extension alone never establish compatibility. A requested network may be admitted only when its basename, size, and SHA-256 all match the frozen identity and a fresh internal evaluator probe succeeds.

## Gate effect

Passing the adapter contract makes only the exact adapted Fairy comparator eligible for the separately preregistered local panel. It does not establish Elo, authorize OpenBench, select a champion, merge product code, or support a release claim. Corrections to this ADR or its fixture must be additive and hash-pinned.
