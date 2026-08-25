# ADR-0004: Exact referee profile and embedded conformance path

- Status: Accepted for fixture-first implementation
- Date: 2026-08-14
- Profile: `LICHESS_CRAZYHOUSE_2026_08_12`
- Evidence class: `E1_ENGINEERING`
- Architecture review receipt: `../../../receipts/private/p4-official-g4-consultation-032.json`

## Decision

The G4 referee candidate is a constrained descendant of the existing AndyGrant/CuteChess production fork. It will add an explicit, versioned Lichess Crazyhouse profile and an embedded machine-readable conformance mode to the exact match executable.

The conformance mode and ordinary match mode must share board construction, FEN and move parsing, move generation, make/history updates, repetition identity, result policy and typed terminal-reason calculation. A separate probe implementation, post-processing sidecar or second live referee cannot satisfy G4.

The current production artifact, SHA-256 `1c0bbab69e15a277c0b68bf032848b513f706749999cd5f6d09a1fb60f05b8a6`, is frozen as `KNOWN_NONCONFORMING`. It accepts the variant but inherits orthodox result policies. It remains a negative baseline and can never be relabeled as the certified artifact.

This decision does not alter the engine source baseline. Crazyhouse-Stockfish remains descended from official Stockfish development commit `5062aee519a1ba262d472d8ab139851ced56573e`. The referee is a separate pinned tool lineage. Fairy-Stockfish and pyffish remain optional correlated observations and cannot enter the engine or referee dependency graph.

## Frozen contracts

The exact profile contract is `tests/crazyhouse/g4-profile-v1.json`, 5,486 bytes, SHA-256 `d0602bc32877639f2d9a70741614882512083431b48b9f4e98a88e1067eb4d68`.

The portable 48-case rule core is admitted unchanged as `tests/crazyhouse/reference-cases.json`, Git blob `69ff656153aa7bb1847eca364ec14047267a6fab`, 17,810 bytes, SHA-256 `4a00bca20d3b149b5bbe3f4153a4a3ff5a20473126763c2d8125a4ba2d11742e`. Its origin is fixture provenance only; retired donor engine results do not satisfy the official-base participant.

The capacity addendum is the existing complete 303-move fixture `tests/crazyhouse/max-moves-303.json`, 4,541 bytes, SHA-256 `3c77de3377d66feecdffd37459e31a1824424b3833a39e2272b4202d1c312e38`. It proves that 256 is invalid and does not claim a global maximum.

The first referee-specific set is `tests/crazyhouse/g4-referee-cases-v1.json`, 4,572 bytes, SHA-256 `6768fa3fb0ff5f3128fe55e1e1beea96aa0e479adf6a8c774dc50b77839f5d7b`. It freezes configuration failures, disabled orthodox results, full-history repetition boundaries, initial terminal states, timeout behavior and sequential/concurrent isolation before referee implementation output is observed.

The mandatory participant/applicability matrix is `tests/crazyhouse/g4-participant-matrix-v1.json`. A participant either returns every declared record or fails. Runtime skips, partial output, duplicate IDs, warnings, truncation and standard-chess fallback are failures.

## Required design

Certified mode requires an exhaustive typed dispatch between standard chess and `crazyhouse + LICHESS_CRAZYHOUSE_2026_08_12`. Missing, unknown or mismatched profiles fail before engines start. A generic default profile is forbidden.

The Crazyhouse result policy selects its predicates before evaluation. It cannot call orthodox insufficient-material, halfmove or repetition adjudication and then repair the result. Timeout material evaluation is profile-owned; the pinned scalachess implementation makes `Crazyhouse.opponentHasInsufficientMaterial` always false, so a flagging side loses even in a kings-only physical state.

Probe and match output carry the profile ID and exact profile SHA-256. Terminal reports are typed and include final canonical FEN, result, reason and a history digest where relevant. The disabled orthodox reasons must be unreachable, not merely absent from one corpus run.

The exact referee must verify a fail-closed engine capability acknowledgement after configuring `UCI_Variant=crazyhouse` and the contract identity. Missing or wrong acknowledgement, configuration timeout, process exit or unsupported option is a configuration failure. The runner never retries as standard chess.

## Certification ladder

1. Reseal the three pinned references on authenticated exports and deterministic normalized output.
2. Project the complete shared corpus through the official-base engine direct and UCI paths while Crazyhouse worker search remains disabled.
3. Map every result, timeout, history, board-factory and move-container path in the exact CuteChess source before changing behavior.
4. Prove the frozen baseline artifact red on the known policy/profile cases.
5. Implement only typed profile selection, profile-owned terminal evaluation, embedded conformance mode and contract identity output.
6. Require mutation tests that re-enable orthodox material/halfmove logic, omit pocket or promoted history state, and restore a 256-move truncation.
7. Run the complete corpus through the optimized exact match executable twice with byte-identical normalized results.
8. Certify match mode with deterministic rule-free UCI actors, raw transcripts, PGN, replay, typed reasons, clocks and game isolation.
9. Produce two clean reproducible referee builds and local exact-artifact T1/T2 receipts.
10. Request owner authorization before publishing/deploying referee bytes or launching one official two-game non-strength canary at `https://belzedar.duckdns.org`.

## Gate boundary

Local success can reach only `G4-LOCAL-CERTIFIED-REFEREE-CANDIDATE`. Full G4 remains open until the exact deployed bytes pass the authorized official canary with raw logs and PGNs. Nothing in this ADR enables Crazyhouse worker search or establishes Elo, OpenBench strength, release readiness or stable-publication authorization.
