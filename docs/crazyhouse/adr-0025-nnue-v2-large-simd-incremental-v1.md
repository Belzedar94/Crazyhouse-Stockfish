# ADR 0025: large-V2 SIMD and transactional incremental accumulator

- Status: implemented; exact-head public CI pending
- Date: 2026-08-29
- Evidence class: `E1_ENGINEERING`
- Architecture: `CH-NNUE-V2-LARGE-K64G1-SFNNV16`
- Product evaluator: Legacy Crazyhouse V1 remains the only default

## Decision

The owned large-A0 datapath adds one explicit SIMD backend and one
network-bound incremental accumulator. Neither is reachable from the normal
engine route at this checkpoint.

The SIMD backend is `sse2-x8-int16-to-int32`. It sign-extends eight serialized
int16 K64 or G1 transformer weights into two groups of four int32 lanes and
adds them to committed int32 accumulators. There is no silent scalar fallback:
an unavailable backend returns `SIMD_UNAVAILABLE`. Dense inference continues
through the arithmetic frozen by ADR 0024, so acceptance requires the complete
scalar and SIMD traces to be identical.

The incremental owner stores, for both perspectives:

- exact K64 and G1 membership bitsets and active counts;
- the authenticated total number of physical pocket units;
- committed int32 K64 and G1 accumulators; and
- the exact network object to which those values are bound.

An update accepts complete source and target inventories. It first validates
both inventories, authenticates the source against committed membership and
computes K64/G1 set differences. All additions and removals are applied to an
int64 candidate. Only after every resulting lane fits int32 are the target
membership, pocket count and accumulators committed together. Any error leaves
the prior state and network binding unchanged.

## Verification contract

The preregistration is
`tests/crazyhouse/p12-nnue-v2-large-simd-incremental-v1.json`, SHA-256
`f8c944b7d6b519f6272ead4dff46e04dc0fdf7318d2bbc9494fefd729d6788bf`.
It binds the immutable 13-case physical transition corpus at SHA-256
`1f93f28118478e46362b4254df7e2fa366b851f698f7c1075676a973f7e80a34`.

The matrix covers quiet and king moves, captures into pockets, promoted-piece
capture provenance, both-color drops, successive cumulative pocket slots,
en-passant, promotion, promoted-piece movement, castling, a capture/drop chain,
null move, make/undo and null undo. Its frozen totals are 49 physical position
checkpoints, 98 side-to-move evaluations, 420,616 scalar/SIMD trace values and
420,616 incremental/full-refresh trace values. Twelve operation failures and
four evaluation failures must be typed, trace-empty and transactional.

Local warning-strict AVX2 verification passed the complete matrix while
preserving six independent scalar traces and 29 adversarial container rejects.
Public Linux, Windows and sanitizer jobs remain required before this subgate
can be recorded as passed.

## Boundary

This decision does not admit a dataset, trainer, checkpoint, resume, network
selection, Elo claim, G12 closure, default-route change or release evidence.
Production training remains blocked until exact-head public CI passes and the
separate deterministic trainer/resume contract is implemented.
