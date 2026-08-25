# ADR-0003 addendum 002: incremental counter semantics

- Status: accepted after disposable implementation preflight
- Date: 2026-08-22
- Evidence class: `E1_ENGINEERING`
- Applies to: `LegacyCrazyhouseAccumulatorStackV1::Counters`

## Pinned boundary

This clarification preserves the architecture and admission corpus frozen by
ADR-0003 addendum 001 (5,142 bytes, SHA-256
`6bb79520c56d58555982d4c669ccedb4f57dd3cdd591dbd9751d6d9d4463d0c1`)
and `legacy-incremental-cases-v1.json` (6,814 bytes, SHA-256
`ba18990faaadcb4fe92b87f8396441f249cf31c1cc6bc98d8912af0a04aa841b`).
The frozen C++ fixture remains 12,252 bytes with SHA-256
`b75d723cb9bf097883d648a85a2e0b97a47bf2691fc8ac143fcdccbd847e81bf`.

The verifier-only correction is independently recorded by
`legacy-incremental-cases-v1.addendum.001.json` (3,761 bytes, SHA-256
`784fffe5ba8f0121a567bb8de8bba8fd5f6a31ea6fd26dc0c8f9210b17b527bf`).
With that correction applied, the disposable diagnostic at
`D:/Crazyhouse-Stockfish/preflight/p5-legacy-incremental-counter-15bfa0f8-001`
reached the unchanged C++ oracle and rejected implementation commit
`15bfa0f8c12c1b61f88f3148fef23c89f1f91dc4` at
`CH-INCR-LAZY-THREE-PLY delta-update counter mismatch`. Its 159-byte stderr has
SHA-256 `f5516ad2624101e067b98dab6cc976b5a1592f0fff3a6da7d6d05308739bb80a`.
No timing or gate claim is admitted from that reused diagnostic binary.

The owner Oracle waiver remains in force. No Oracle, API, credits, fallback,
OpenBench resource, or foreign process is used by this clarification.

## Decision

The counters have separate, non-overlapping meanings:

- `deltaUpdates` counts successful frame materializations that copied one
  computed ancestor and applied a sparse set difference. It increments once
  per such evaluation, regardless of ancestor distance or feature-row count.
- `maxSourceDistance` records the maximum number of stack frames between the
  materialized target and its nearest computed source.
- `addedFeatures` and `removedFeatures` count the actual perspective-specific
  sparse row operations.
- `fullRefreshes` counts materializations with no computed ancestor.
- `sameFrameReuses` counts successful propagation from an already computed
  current frame without physical row mutation.

For the frozen three-ply lazy case, the leaf is materialized from the root at
distance three. Undo then materializes the two previously skipped intermediate
frames at distances two and one. The required totals are therefore three
`deltaUpdates` and `maxSourceDistance == 3`; adding source distance to
`deltaUpdates` would incorrectly total six.

This addendum changes no evaluator arithmetic, feature rows, network, move,
position, make/undo, null behavior, test expectation, search bound, or release
claim. A fresh clean export must pass the complete fixture twice before the
implementation can be admitted.
