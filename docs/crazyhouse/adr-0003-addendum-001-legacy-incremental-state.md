# ADR-0003 addendum 001: transactional legacy V1 incremental state

- Status: frozen before implementation
- Date: 2026-08-22
- Evidence class: `E1_ENGINEERING`
- Applies to: `LegacyCrazyhouseNetworkV1` only

## Authority and consultation boundary

This addendum is based on official Stockfish development commit
`229f6339e537a097a79831cd06dbfdb3e623d4ac`. Fairy-Stockfish remains forbidden
as a source base. The registered legacy network remains the 58,534,811-byte
artifact with SHA-256
`8ebf84784ad20fa33df403e60211818a7486db7cb8c3decfc86a80238d254f43`.

The owner explicitly waived the otherwise mandatory browser Oracle consultation
for this continuation on 2026-08-22. No API, credits, or fallback consultation
is used.

Atomic-Stockfish commits `09f5e6c1ba088a234ce810f80ce69d6ed4f7b465`
and `0e3cab3dfb46f41b8d2d7bd3522cc01e53c23aaa` were inspected for transactional
incremental validation and stress-test method. Horde-Stockfish commits
`6cc4c218c`, `bb5ebfda5`, and `36810cc58` were inspected for an engine-owned
external accumulator stack, lazy materialization, null handling, and exact
incremental/full-refresh comparisons. These are method references only. No
Atomic or Horde rules, features, networks, dimensions, buckets, fixtures, or
performance claims are inherited.

## Decision

Legacy V1 receives a dedicated worker-owned accumulator stack. It does not
reuse or enlarge official Stockfish's current NNUE accumulator frames and it
never stores evaluator state in `StateInfo`.

The stack mirrors real moves with one `push` and one `pop`. A null move does not
create a frame because it changes neither the V1 board inputs nor pockets; its
side-to-move-dependent dense composition is still recomputed. Stack overflow,
underflow, network-object replacement, wrong ruleset, invalid physical inputs,
and a same-frame physical feature mutation fail closed.

Each computed frame owns:

- the exact canonical sorted active V1 row set for both perspectives;
- both 512-lane transformer accumulators as modulo-`2^16` bit patterns;
- both eight-bucket PSQT accumulators as modulo-`2^32` bit patterns;
- board-piece count and both king squares;
- a computed flag only after transactional completion.

Evaluation re-extracts the exact target V1 feature set from the current
`Position`. If no earlier computed frame exists, it performs the certified
scalar full refresh. Otherwise it copies the nearest computed ancestor and
applies the sorted-set removals and additions directly. A king-square change
therefore replaces all rows for that perspective without a special inferred
source position. Pocket count changes replace the exact cumulative pocket
slots. Drops, promoted-origin captures, en-passant, promotion, castling, and
ordinary captures are consequently derived from the complete committed
physical target rather than from a board-only delta.

This deliberately supersedes ADR-0001's proposed V1 extension of the shared
`Dirties` payload. The existing `DirtyPiece` is insufficient to authenticate
pockets and promoted-origin consequences, while duplicating full variant state
inside every official NNUE frame would couple the two backends. A future V2
physical-delta contract remains a separate P12 decision and must represent
pockets and promoted provenance explicitly.

The delta operation uses the same registered parameter arrays and explicit
wrapping arithmetic as scalar full refresh. Dense propagation and the outer
legacy adapter remain shared code. The candidate frame and counters commit only
after feature validation and raw propagation succeed.

## Fixture and admission contract

The immutable case plan is
`tests/crazyhouse/legacy-incremental-cases-v1.json` (20 cases, SHA-256
`ba18990faaadcb4fe92b87f8396441f249cf31c1cc6bc98d8912af0a04aa841b`). It binds the existing
numeric golden corpus and the shared Crazyhouse rule fixture, and adds exact
walk, lazy, null, undo, branch, and desynchronization schedules.

Admission requires all of the following in debug and release-capable builds:

1. incremental raw PSQT and positional output equals scalar full refresh for
   all eight buckets after every evaluated transition;
2. the selected bucket and complete legacy outer adapter are identical;
3. every undo returns to an already authenticated frame and remains identical;
4. null move and null undo reuse physical rows while recomputing the correct
   side-to-move composition;
5. a lazy sequence spanning multiple unevaluated plies reports the frozen
   source distance and remains identical;
6. drop, successive pocket count, capture-to-pocket, promoted-origin capture,
   en-passant, promotion, promoted-piece motion, king motion, castling, and
   capture-then-drop cases pass;
7. a physical move without a stack push and a different network object are
   rejected without committing a frame or counters;
8. the production worker uses the incremental entry point, while the frozen
   standard-Chess control remains byte-for-byte behaviorally unchanged.

No result from this gate is speed, Elo, model selection, OpenBench, or release
evidence. SIMD remains forbidden until this incremental/scalar gate is green.
