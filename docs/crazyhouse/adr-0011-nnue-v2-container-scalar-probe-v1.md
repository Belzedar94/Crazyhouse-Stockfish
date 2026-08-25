# ADR 0011: Crazyhouse NNUE V2 quantized scalar probe container

- Status: accepted and preregistered before implementation
- Date: 2026-08-24
- Evidence class: `E1_ENGINEERING`
- Rule profile: `LICHESS_CRAZYHOUSE_2026_08_12`
- Base commit: `fb852bbe06a1585dc01120bcbbc3b313a258e160`
- Base tree: `20859d3aaa33d49b808bf0b332e3faf8af235960`
- Base `src` tree: `958e5fa2707e60147884f43ed1bfe1c7f29ca230`
- External advisory review: explicitly waived by the owner for this phase; no API fallback is permitted or used

## Context

The first P12 checkpoint proved a fail-closed physical decoder and the 902-row
scalar full-refresh inventory, but it did not serialize quantized parameters or
compare trainer arithmetic with C++. A production topology cannot be selected
before frozen data and model-selection ablations exist. The next checkpoint
therefore uses a deliberately small, odd-width scalar probe whose only purpose
is exact parser and integer-layer parity.

Legacy Crazyhouse V1 remains the productive/default evaluator. The probe is not
linked into the normal engine and cannot be selected through UCI.

## Decision

Two immutable schemas are introduced before any container bytes:

- `crazyhouse-nnue-v2-features-v1`, SHA-256
  `1e2b9afc2be77d2df66e3cdfe22bffafa7f2d926b224d2b01ab244f354c889c6`;
- `crazyhouse-v2-scalar-probe-container-v1`, SHA-256
  `5fe00bb91876650fb768c6b8bc80eacbb1ca2a16f631c528f803dcc8965ec7a3`.

The container has unique 16-byte magic, a fixed 256-byte little-endian header,
30,668 bytes of row-major signed int16 weights and 68 bytes of signed int32
biases. Its total is exactly 30,992 bytes. The header binds the rule profile,
physical schema, feature contract, architecture descriptor and payload SHA-256,
then protects its own first 252 bytes with CRC32C. Exact offsets, dimensions,
types, lengths, flags and reserved bytes are mandatory; trailing or partial
bytes fail.

The architecture descriptor is exactly:

```text
Crazyhouse-Stockfish scalar probe v1|features=902|max_active=138|lanes=17|input=sparse-binary|weights=int16-le|bias=int32-le|accumulator=int32
```

Its 142 UTF-8 bytes have SHA-256
`e71d819a1d568979ec4fe99b6a004359768c31f618c91da7a309386f3bf732bb`.
Seventeen lanes are intentionally odd and non-SIMD-aligned. They are a parity
probe width, not a productive width or model-selection candidate.

### Integer arithmetic

Each lane starts at its signed int32 bias. Every active sparse-binary feature
adds the corresponding signed int16 weight using checked int32 arithmetic. The
synthetic artifact has no RNG:

- weight `(row, lane) = ((row * 131 + lane * 17 + 23) mod 257) - 128`;
- bias `lane = ((lane * 1009 + 7) mod 2001) - 1000`.

The frozen active-row capacity and value ranges bound the absolute accumulator
at 18,664, well inside int32. No scaling, activation, output head, loss or
playing evaluation is implied.

### Independent sides

The trainer-side Python module must not import the existing physical codec or
C++ implementation. It independently checks record framing/CRC/position
identity, enumerates the frozen 902 rows, serializes and parses the probe, and
evaluates its integer layer. The C++ side reuses the admitted physical decoder
and scalar feature inventory, but owns a separate fail-closed container parser
and scalar evaluator. Only their final ordered rows and 17-lane int32 outputs
are compared.

## Frozen verification contract

Before implementation results are observed, the checkpoint requires:

1. deterministic synthetic bytes of exactly 30,992 bytes, pinned by additive
   addendum before the formal run;
2. Python serialize → parse → reserialize byte identity and C++ parsing of the
   same exact artifact;
3. 42 physical goldens, five pocket/provenance/symmetry controls and one
   capacity control: 48 records, 96 perspectives and 1,632 lane values;
4. exact ordered feature-row and int32 output parity between independent Python
   and C++ paths;
5. thirty named parser mutations covering framing, header constants, types,
   offsets, both reserved regions, every bound identity, CRC and payload hash;
6. no partially usable network object and no fallback on every rejection;
7. release and assertion-enabled clean-export builds reproduce the same
   protocol digest;
8. the normal engine still routes to Legacy V1, benches at 113485 and contains
   no probe-container symbol/string.

CH-263 remains an open toolchain debt. This checkpoint does not repeat or waive
the failed sanitizer profile; sanitizer credit requires a separately admitted
compiler, linker and runtime.

## Explicit non-claims

This probe does not select a production topology, hidden width, activation,
quantization scale, accumulator update strategy, SIMD layout, trainer optimizer,
dataset, model or champion. Its synthetic weights are not a network candidate
and may never enter a match. It does not close G12 and is not training, loss,
Elo, OpenBench or release evidence.
