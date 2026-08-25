# ADR-0004 Addendum 003: Referee source map and fixture closure

- Status: Accepted before referee behavior implementation
- Date: 2026-08-14
- Evidence class: `E1_ENGINEERING`
- Profile: `LICHESS_CRAZYHOUSE_2026_08_12`
- Profile SHA-256: `d0602bc32877639f2d9a70741614882512083431b48b9f4e98a88e1067eb4d68`

## Decision

The Crazyhouse referee candidate starts from the clean upstream CuteChess commit `24d4301152fb92ac442425e083a2658225f80720`, tree `289867c000147a84b2a48eff5ce1aa2fbd85e168`. Read-only mapping of that exact tree identified 34 production source files and 12 behavior boundaries before any candidate branch or behavior edit. The source map is `tests/crazyhouse/g4-referee-source-map-v1.json`, 15,041 bytes, SHA-256 `51a5dd5b5ca052a3dd8d49de4e593cf632074638af61d2877ebc7892e2d17863`.

The original 14 referee cases remain immutable. `tests/crazyhouse/g4-referee-cases-v1.addendum.001.json`, 19,947 bytes, SHA-256 `e984177d4e377bfa2864ce94a9b59b0f50fc23d2a2e96650493b38c3fa8fa551`, freezes 42 additional uniquely named fixture objects. `tests/crazyhouse/g4-participant-matrix-v1.addendum.002.json`, 6,309 bytes, SHA-256 `0087816f7b4c549cd8b4dbeb1ef9fb54e9d00ee2fd7da4c27bdcb021f30cf4d2`, assigns every new boundary to its mandatory independent references, exact embedded probe, exact match path, production engine UCI path or rule-free actor.

Corrections to these files require a new hash-pinned addendum. Candidate output cannot rewrite a fixture, supply a missing expectation or convert a skipped participant into a pass.

## Frozen event and protocol contracts

`MOVE_READY` is the single normative clock event. It exists only after a complete newline-terminated `bestmove` frame passes strict Crazyhouse UCI grammar, converts to a legal move in the current physical position and receives its monotonic timestamp. A legal `MOVE_READY` exactly at the deadline is accepted and outranks an equal-timestamp timeout regardless of callback delivery order. Incomplete, malformed or illegal input at the deadline does not outrank timeout. State, increment and history change only after an accepted event wins arbitration.

Every engine process and game configuration barrier uses a fresh 32-character lowercase hexadecimal nonce. After exact `UCI_Variant`, `CrazyhouseProfile` and `CrazyhouseCapabilityNonce` configuration, the engine must emit exactly one nonce-bound `crazyhouse_capability_ack` after the matching `isready` and before `readyok`. Missing, malformed, stale, early, late, duplicate or conflicting acknowledgements fail before `ucinewgame`, `position`, `go` or clock start. No standard-chess fallback is permitted.

The referee accepts the frozen bracket-pocket canonical FEN and ninth-slash pocket ingress forms, serializes only canonical bracket-pocket six-field FEN, and rejects malformed pockets and logical field counts atomically. Match `bestmove` accepts only strict UCI coordinate, promotion and drop forms, with an optional valid `ponder` clause and no trailing tokens. General SAN parsing remains available only where PGN requires it; it cannot admit engine moves.

## History, result and PGN join

The history digest is SHA-256 over exact UTF-8 bytes without BOM, LF line endings and a final LF. Its header binds schema, profile ID and profile-file SHA-256. It records the canonical root and every accepted legal move with its resulting canonical physical FEN. Four literal vectors bind byte counts and digests, including two paths with equal root and final position but distinct history hashes.

Crazyhouse result policy does not inherit orthodox insufficient-material, 50-move, 75-move or automatic threefold results. The frozen profile retains checkmate, stalemate and automatic fivefold precedence plus the separately controlled optional match-claim proxy. Timeout is a loss even in kings-only physical states.

PGN must carry semantic `Variant`, profile ID, profile SHA-256, typed reason and history SHA-256 tags. Replay must reproduce every canonical per-ply state, move, result and history digest. Formatting, tag order and movetext wrapping are non-normative.

## Authentication

The behavior-independent verifier is `tests/crazyhouse_referee_fixture_contract.py`, 18,410 bytes, SHA-256 `3db4de3491c111b4240820d871d2296c63949da3d35d7600a1df74ace560c206`. It rejects duplicate JSON keys and fixture IDs, pin drift, malformed digest vectors, capability-order drift, source-map gaps, dirty or shallow referee roots, replace refs, grafts, the forbidden Horde-derived object and any of the 34 mapped source-file byte or hash mismatches.

The verifier passed both its repository-portable mode and its private-receipt authentication mode against the clean upstream root. The complete private mode authenticated 56 fixture IDs, four history vectors, 34 source files and 13 pins with zero skipped external pins. The advisory review and independent two-reference golden derivation are pinned respectively by `../../../receipts/private/p4-referee-fixture-closure-consultation-040.json`, 3,994 bytes, SHA-256 `7d0f515bcef1387cf7a79f84cafb1495009ccbe1cc383074590ff04ec8862e46`, and `../../../receipts/private/p4-referee-fixture-goldens-041.json`, 5,423 bytes, SHA-256 `aef60bc8a408aae7e35ff074f703a4c02876b4142f647fb482b818d6679b941b`.

## Implementation boundary

No referee behavior source was changed while this addendum was frozen. The next admissible action is a linear project-authored candidate branch from the authenticated clean root. The embedded probe and ordinary match must be two modes of one optimized executable and must share the mapped production board, FEN, move, make/history, repetition, result, clock, notation and PGN paths.

This closure is an engineering-fixture result only. It does not certify referee behavior, enable Crazyhouse search, provide strength evidence, authorize OpenBench work or advance release readiness.
