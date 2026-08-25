# ADR-0003 Addendum 001: Legacy V1 SIMD parity boundary

- Status: Accepted for fixture-first engineering
- Date: 2026-08-23
- Evidence class: `E1_ENGINEERING`
- Applies to: `LegacyCrazyhouseNetworkV1` only
- Production dispatch at this boundary: scalar incremental, unchanged

## Decision

The registered legacy V1 artifact remains a canonical, evaluator-specific byte container. The
parser continues to populate the existing canonical scalar tensors and validate the exact
58,534,811-byte artifact and SHA-256. A second runtime layout may be hydrated from the same
authenticated layer bytes solely for optimized propagation. It must use the current official
Stockfish `Layers::AffineTransform` implementation from the pinned official-development
ancestor; no Fairy-Stockfish generic network object or source ancestry enters the product.

The optimized evaluator is an explicitly selected backend. `SCALAR` remains the constructor
default and the production Worker route remains `incremental-scalar` until a later, separately
authenticated dispatch change. An optimized backend compiled without an admitted SIMD lane must
reject evaluation; it must not silently execute the scalar backend while claiming SIMD.

The first admitted SIMD target family is x86-64:

| Build lane | Required compiled backend | Transformer and PSQT operations | Dense layers |
| --- | --- | --- | --- |
| `ARCH=x86-64` | `sse2` | wrapping SSE2 add/sub and exact saturating transform | current official Stockfish affine path |
| `ARCH=x86-64-avx2` | `avx2` | wrapping AVX2 add/sub, SSE2 ordered packing | current official Stockfish affine path |

Other architectures retain the scalar production backend and receive no SIMD claim until their
own target-matrix fixture and runtime evidence exist. AVX-512 builds may reuse the admitted x86
accumulator operations while the official affine layer selects its compiled backend, but they do
not receive independent AVX-512 gate credit from this contract.

## Arithmetic and layout invariants

The scalar reference is normative for integer results. SIMD must preserve:

1. modulo-2^16 transformer accumulation and modulo-2^32 PSQT accumulation;
2. signed reinterpretation before clamping transformer lanes to `[0, 127]`;
3. canonical file order and scalar tensor order, irrespective of private runtime weight
   permutations used by the official affine implementation;
4. all eight PSQT and positional bucket outputs, the selected bucket, and every legacy outer
   adapter field;
5. fail-closed network, ruleset, stack-ownership and unsynchronized-state behavior.

The optimized runtime layout is rebuilt transactionally during a candidate load. A failure to
hydrate it rejects the candidate and cannot retain an earlier usable backend. It is never
serialized as a replacement network and cannot change the public alias bytes.

## Fixture-first parity gate

The frozen 20-case physical corpus is replayed independently through scalar and SIMD network
objects and stacks. Exact equality is required after each root, move, lazy chain, drop, capture,
promotion, promotion capture, undo, null move and rejected unsynchronized mutation. Both modes
must reproduce 27 transitions, 27 undos, one null case, the frozen counter expectations and the
same deterministic trace digest. The boundary controls for a stack bound to another network and
for orthodox Chess must reject identically.

Debug and release builds are independent. SSE2 and AVX2 outputs must be byte-identical after line
ending normalization. A scalar build, a compile-only success, a benchmark, a sanitizer run or a
single position cannot close this gate. After parity, the existing Worker/routing and exact
official-standard controls must be replayed with an engine-authored `incremental-simd` token
before production dispatch can change.

## References and non-inheritance

The pinned current Stockfish affine and SIMD headers are implementation authorities for the
official-development source line. The clean Horde repository demonstrates the method of keeping
canonical loader bytes separate from an optimized affine layout, and the clean Atomic repository
demonstrates an independent AVX2 compiler lane. They are references only: no Horde or Atomic
network, feature definition, rule value, target claim, test result or release identity is inherited.
The inspected Fairy Vault contained no directly applicable Crazyhouse SIMD contract and was not
used as source.

## Claim boundary

Passing this addendum proves integer evaluator correctness for the explicitly built SIMD lanes.
It is not timing evidence, NPS, Elo, model selection, OpenBench evidence, packaging, release or
post-release monitoring. Speed is measured later on an otherwise clean host with repeated paired
blocks; strength remains a separate `S3_STRENGTH` decision.
