# ADR-0011 addendum 001: V2 SIMD and transactional incremental probe

- Status: frozen before implementation
- Date: 2026-08-24
- Evidence class: `E1_ENGINEERING`
- Applies to: test-only `CHNNUEV2REF1` scalar-probe execution
- Production dispatch: unchanged legacy V1

## Boundary and authorities

This checkpoint starts from Crazyhouse-Stockfish commit
`50fd7a3359e5b8dc6f05913d2fb8fe65bca80e29`, tree
`98a00d063dd30f1b7ab60ff2ad3051478818bfda`. Its source lineage remains the
official Stockfish development line. Fairy-Stockfish remains forbidden as a
source base. The owner waived Oracle for this continuation; no API, credits, or
fallback consultation is used.

The existing V2 container/scalar preregistration and result are bound by
SHA-256 `3610b737bd5396c64a88450590b77b80b48cd052b72e8622b2e02ec7fa1c93c4`
and `722faae7e0a71da33da12131af1bd60c67b1050a93d34fd07b217239dec1248d`.
The synthetic artifact remains 30,992 bytes with SHA-256
`fdd55e1a6af735cf1e999af31341c249c52f444f553454606195124a34b07d12`.
This addendum does not change the container, feature dimensions, weights,
biases, or architecture identity.

Atomic-Stockfish worktree commit
`13a1cf845c51eee3507ed6baa080895014de9e8b` and Horde-Stockfish worktree
commit `83521c3b9ff2c9e195b8fe75c3b8ec4917bd0e02` were inspected read-only for
their exact-refresh comparison and transactional-state test methods. Atomic's
worktree was clean on its DATAGEN branch. Horde's worktree was clean but four
commits behind `origin/main`, so it is not treated as current authority. No
rules, positions, feature shapes, networks, cases, or performance claims are
inherited from either project.

## SIMD decision

The dedicated probe receives an SSE2 execution path for the first 16 of its 17
signed-integer lanes. Lane 17 is an explicit scalar tail. The test target must
report `sse2-x16-scalar-tail1`; an unavailable SIMD backend is a hard failure
and never falls back silently.

Weights are sign-extended from serialized `int16` values and accumulated in
signed 64-bit lanes before the existing `int32` range check. This preserves the
scalar overflow contract instead of relying on wrapping SIMD arithmetic. The
frozen parity corpus checks both perspectives, all 17 biases, every one of the
902 serialized feature rows in isolation, and deterministic multi-row sets at
the declared 138-row capacity. Duplicate, out-of-range, over-capacity, invalid
status, invalid perspective, and not-ready inputs must be rejected identically.

## Incremental decision

The test-only accumulator owns exact per-perspective feature membership and all
17 accumulated lanes. It binds to one ready network object. It stores no state
in `Position`, `StateInfo`, or official Stockfish's NNUE accumulator.

An update accepts complete authenticated source and target inventories. It
first proves that its committed membership is exactly the supplied source,
then computes removals and additions from the full inventories. Board pieces,
pocket count slots, and promoted-origin rows are therefore handled by their
actual Crazyhouse state, not by an orthodox move-type inference. Candidate
membership and lanes commit only after source, target, network identity, and
signed `int32` bounds all pass. Any failure leaves the accumulator unchanged.

The frozen transition corpus exercises, for both perspectives, quiet motion,
king motion, ordinary capture into a pocket, capture of a promoted-origin
piece, white and black drops, successive pocket consumption, en-passant,
promotion, promoted-marker motion, castling, capture-then-drop, make/undo, and
null/null-undo. Every committed state is compared with both scalar and SIMD
full refresh. A stale source inventory, a different network object, an invalid
inventory, and a not-ready network are explicit negative controls.

## Admission and isolation

The immutable case contract is
`tests/crazyhouse/p12-nnue-v2-simd-incremental-probe-v1.json`. Admission
requires:

1. an expected-red run before implementation because the SIMD/incremental test
   target does not yet exist;
2. exact scalar/SIMD equality across every frozen lane comparison, including
   the odd tail and exhaustive single-row coverage;
3. exact incremental/full-refresh equality after every make, undo, null, and
   null undo for both perspectives;
4. unchanged accumulator output and membership after every negative control;
5. expected final FEN and exact root restoration for every transition case;
6. byte-identical normalized output over two runs in release and debug builds;
7. warning-clean, clean-export builds with source manifests unchanged before
   and after compilation; and
8. proof that the normal engine remains routed to legacy V1 and contains no V2
   probe identity strings.

The dedicated target is not part of normal engine `SRCS`. This result cannot
close G12: it is not a productive V2 topology, trainer, deterministic resume,
legacy-through-V2 control, strength result, OpenBench result, or release
artifact. The existing sanitizer runtime gap also remains open.
