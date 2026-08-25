# ADR 0010: Crazyhouse NNUE V2 physical decoder and scalar feature inventory

- Status: accepted and preregistered before implementation
- Date: 2026-08-24
- Evidence class: `E1_ENGINEERING`
- Rule profile: `LICHESS_CRAZYHOUSE_2026_08_12`
- Product source commit: `24ffe45e9426c0a0f378c604d026e7482314b2cb`
- Product source tree: `86f1feb87d5576088af4aba1cc389ac2dde83111`
- Product `src` tree: `834827c801093b49bc1616a866757de5a994a06a`
- External advisory review: explicitly waived by the owner for this phase; no API fallback is permitted or used

## Context

The productive evaluator remains Legacy Crazyhouse V1 and must remain the
default until a real quantized V2 artifact passes engineering, model-selection
and strength gates. The first V2 checkpoint therefore cannot select a network
topology or consume fixture labels as training data. It must prove that the
frozen physical records can be decoded fail-closed and projected into an
injective scalar inventory that distinguishes board content, exact pocket
counts and promoted-piece provenance.

The canonical input remains `crazyhouse-physical-v1`, schema SHA-256
`c72a1fac41e311ed09a2167c56887d64b18293149291f6505f4021f348c1ef55`.
The 42 checked-in records are synthetic schema goldens and remain inadmissible
for training or model selection.

## Decision

This checkpoint adds a test-only C++ physical-record decoder and a scalar
full-refresh feature enumerator. Neither implementation is linked into the
normal engine target. No UCI option, evaluator routing, default network or
search behavior changes.

### Decoder boundary

The decoder accepts exactly one 256-byte little-endian
`crazyhouse-physical-v1` record and commits an output object only after all
checks pass. It preserves:

- absolute board and ten absolute-owner pocket counts;
- promoted-origin mask, side to move, castling rights, raw/effective
  en-passant, repetition and claim state, clocks and terminal reason;
- move wire, absolute-White and side-to-move results, teacher metadata;
- sequence, game/trajectory/ply and all three stored digests.

It rejects wrong framing, magic/version, CRC32C, reserved bytes, flags, piece
codes, king counts, pawn ranks, promoted-mask ownership, pocket bounds,
castling/EP coherence, enums, move-state coherence, label perspective,
teacher framing, zero identities and a recomputed physical-position SHA-256
mismatch. Cross-record history continuity and full move legality remain chunk
and referee responsibilities; a record decoder must not claim them.

### Scalar feature inventory

Each perspective receives a deterministic sparse list in three disjoint
blocks:

| Block | Rows | Active semantics |
|---|---:|---|
| Relative board roles | `12 * 64 = 768` | One row per occupied square; type-major `P..K`, own then opponent, with Black rank orientation |
| Exact pocket counts | `2 * (17 + 5 + 5 + 5 + 3) = 70` | Exactly one row for every relative-owner/type band, including count zero |
| Promoted provenance | `64` | One independent oriented-square marker for every set promoted bit |
| **Total** | **902** | No topology, weights or quantization implied |

The pocket maxima are derived from the frozen Crazyhouse physical schema:
`P=16`, `N=4`, `B=4`, `R=4`, `Q=2`. Exact-count one-hot rows are selected over
legacy cumulative slots because zero is explicit and every admitted count has
one unique row. A promoted marker is separate from the visible board role, so
otherwise byte-identical board states with different origin provenance cannot
collide.

Enumeration order is part of this checkpoint ABI: physical board squares
`a1..h8`, pocket types `P,N,B,R,Q` with relative owner then opponent, then
promoted physical squares `a1..h8`. Indices are perspective-oriented, but the
iteration order is physical. The conservative capacity is 138 active rows
(`64 board + 10 pocket bands + 64 promoted markers`) so every structurally
admitted nonstandard record fits without borrowing orthodox piece limits.

The decoder preserves castling, EP, clocks, repetition, claim and history
identities, but this first feature inventory does not assert that those fields
belong in the evaluator input. Any such addition is a separate preregistered
state ablation. Side to move is represented by emitting both perspectives,
not by an extra row.

## Frozen verification contract

Before implementation results are observed, the checkpoint requires:

1. all 42 frozen records decode and retain their authenticated physical
   position identities;
2. an independent Python expectation reproduces all 84 ordered sparse lists;
3. C++ extraction from decoded bytes equals C++ extraction from a real
   Crazyhouse `Position` reconstructed from the same state;
4. board-identical pocket and promoted-marker pairs produce distinct rows in
   both perspectives;
5. rank-reflection plus color-swap symmetry maps White rows to Black rows;
6. adversarial mutations reach named fail-closed decoder errors, including a
   semantic mutation with repaired CRC and an identity mutation;
7. release and assertion-enabled fixture builds pass; admitted sanitizer runs
   report no defect;
8. the normal engine still builds, advertises only the Legacy V1 productive
   path and retains deterministic bench `113485`.

Any implementation identity or correction is pinned by a new additive
preregistration addendum. Existing receipts are never rewritten.

## Explicit non-claims

This ADR does not freeze a king bucket, layer width, hidden topology, output
head, quantization scale, container format, SIMD layout, accumulator delta,
trainer implementation, optimizer, dataset split or champion. It does not
close G12 and is not training, loss, Elo, OpenBench, model-selection or release
evidence.

## Reference boundary

Atomic-Stockfish and Horde-Stockfish were inspected read-only only for the
method of versioned decoding, bounded scalar enumeration, fail-closed errors
and cross-implementation parity. Their schemas, roles, dimensions, topology,
containers and values are rejected as Crazyhouse inputs. The exact refresh is
recorded in `p12-nnue-v2-reference-refresh-v1.md`.

