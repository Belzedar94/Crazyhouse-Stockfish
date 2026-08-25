# ADR-0003 Addendum 003: Legacy V1 SIMD runtime layout

- Status: Accepted after first-difference diagnostic
- Date: 2026-08-23
- Evidence class: `E1_ENGINEERING`
- Applies to: the private optimized layout of `LegacyCrazyhouseNetworkV1`
- Production dispatch: scalar incremental, unchanged

## Context

The first AVX2 implementation hydrated current official Stockfish affine layers from the exact
legacy V1 bytes. Its clean debug build linked without warnings, but the frozen parity corpus
rejected the first position. An explicit canonical-to-lane-order adapter then made the transformed
input, `affine0`, and `hidden0` bit-identical while the result still first diverged at `affine1`.

The legacy container serializes the second affine matrix as 32 outputs by 32 input bytes. Only the
first 16 canonical activation bytes are nonzero, but the scalar reference deliberately iterates all
32 serialized bytes. Current AVX2 paired-activation layout permutes four-byte chunks across that
entire 32-byte block. Consequently an optimized layer declared with 16 input dimensions executes
only half the permuted block and omits real activations.

## Decision

The optimized second layer is `Layers::AffineTransform<32, 32>`. Its logical activation producer
still writes the same 16 canonical values and zero-initializes the remaining bytes. Before
propagation, all 32 bytes are adapted to the exact lane order paired with the load-time weight
permutation. The first and final affine layers retain their existing 1024 and 32 input dimensions.

This changes neither serialized bytes nor the canonical scalar tensors. The parser still validates
the registered 58,534,811-byte artifact and SHA-256 before committing a candidate network. Scalar
remains the constructor and production default.

## Evidence and gate boundary

Private rejection record 140 pins the clean source archive, warning-clean AVX2 build, unchanged
frozen-corpus rejection, and the post-rejection diagnostic. At both the frozen root and `a2a3`, the
trace is equal through transformed features, `affine0`, and `hidden0`; its first differing stage is
`affine1`. The diagnostic namespace is rejected and cannot grant or contribute green gate credit.

The correction is accepted only if fresh clean exports pass the unchanged two-run corpus for SSE2
and AVX2 in both debug and release configurations, reproduce normalized protocol bytes, and then
pass Worker/routing and exact standard-control replay. No result here is speed, strength,
OpenBench, model-selection, packaging, or release evidence.
