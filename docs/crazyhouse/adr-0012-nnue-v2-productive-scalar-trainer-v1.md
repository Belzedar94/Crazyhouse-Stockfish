# ADR 0012: Crazyhouse NNUE V2 productive scalar topology and deterministic trainer kernel

- Status: accepted and preregistered before implementation
- Date: 2026-08-24
- Evidence class: `E1_ENGINEERING`
- Rule profile: `LICHESS_CRAZYHOUSE_2026_08_12`
- Base commit: `b8ab35cf70305b4c3cc720f5ccc828e0baac769a`
- Base tree: `c36ea7c958c7428a272ef5878100359968a690b9`
- Base `src` tree: `5940f07ad6e0e66af0a91d4eb37337cf63ac9005`
- Official Stockfish ancestor: `229f6339e537a097a79831cd06dbfdb3e623d4ac`
- External advisory review: explicitly waived by the owner; no API, credits, fallback, or alternate model is used

## Context

The physical decoder, 902-row inventory, integer probe container, primitive
SIMD path, and transactional primitive accumulator are green local engineering
checkpoints. They do not define or authenticate a playing topology. CH-265
also makes explicit that primitive parity cannot stand in for parity after a
productive topology is integrated.

No training-admissible production dataset exists. The checked-in 42-record
corpus is a physical-format golden and its labels are forbidden for training
or model selection. This ADR therefore freezes a playing-capable scalar
topology and the deterministic execution kernel needed to train and export it,
but permits only an engineering micro-fit whose targets are derived from
position identities and have no chess meaning.

Current official Stockfish source was inspected for integer execution and
overflow methodology. The registered legacy Crazyhouse evaluator was
inspected as a compatibility control. Atomic and Horde snapshots were read
only for fail-closed resume and receipt methods. No foreign feature rows,
schema, rule, topology, bound, optimizer result, network, or training value is
inherited.

## Decision

### Productive baseline topology

The baseline consumes the already frozen 902 sparse binary rows for both
perspectives. One shared `902 x 512` transformer and bias produce two int32
accumulators. After clipping each to unsigned `[0,127]`, the side-to-move half
is concatenated before the opponent half. The dense trunk is
`1024 -> 32 -> 32 -> 1`, with clipped ReLU after the first two dense layers and
one side-to-move scalar output.

The baseline has no PSQT branch, material bucket, king bucket, state-dependent
head, or separate color head. Castling, en-passant, counters, and repetition
remain decoded physical state but are not input rows. Each addition is a
separate future state ablation. Width is likewise a future one-variable
ablation.

The 512-lane transformer is frozen before observing training loss. It keeps
the representation change from being confounded with a narrower transformer
than the admitted legacy control, remains a multiple of all currently targeted
32- and 64-byte SIMD tiles, and still yields a complete fixed container below
one MiB. The 32-lane hidden layers avoid a non-vector-width bottleneck. These
are baseline engineering choices, not claims that the widths are optimal.

Exact contract:

- architecture: `schemas/crazyhouse-nnue-v2-productive-architecture-v1.json`,
  4,548 bytes, SHA-256
  `76ebf73988d21fdd3dbf3c34420be0abe6a587419c9f170c16fa3acde4c112b6`;
- descriptor: 302 ASCII bytes, SHA-256
  `f9ef0b0f404de4e5347514bdc2eedee6c864b48d5da2a9d3056aeb6f4cc7d10c`;
- parameters: 496,225;
- transformer accumulator storage: 4,096 bytes per position;
- fixed file size: 960,324 bytes.

### Quantized arithmetic

The transformer uses int16 weights at scale 127, int32 biases at scale 127,
and int64 working sums checked before int32 commit. Hidden dense weights are
int8 at scale 64; biases are int32 at scale `127 * 64 = 8,128`. Their positive
activation divides by 64 and clips to `[0,127]`. Output weights are int16 at
scale 64, the output bias is int32 at scale 8,128, and signed output conversion
truncates `raw * 600 / 8,128` toward zero to produce side-to-move centipawns.

Float32 export widens each value to float64, multiplies by its exact scale,
and rounds to nearest with half away from zero. NaN, infinity, tensor-shape
drift, serialized-type overflow, or a static integer interval outside int32
rejects the whole export. The loader independently repeats interval proofs for
every output. No wrap, saturation outside the declared activation, partial
object, or fallback is allowed.

The quantization contract is
`schemas/crazyhouse-nnue-v2-quantization-v1.json`, 2,997 bytes, SHA-256
`0a9d811ce76509ab58c1eec02fd87cef9df3804d76eb2fe2ae156183b23311a3`.

### Container

The fixed little-endian container has a 512-byte header and a 959,812-byte
payload. Eight tensors begin at exact 64-byte-aligned offsets except that the
four-byte terminal output bias ends the file. Header identities bind the rule
profile, physical schema, feature contract, architecture contract,
quantization contract, exact dataset manifest, exact training configuration,
and payload. Header CRC32C and payload SHA-256 are mandatory.

Dataset and training-configuration identities may never be zero, including
for engineering artifacts. The engineering fixture uses its own explicit
inadmissible manifest/config identities. A public alias is never an internal
container identity.

The container contract is
`schemas/crazyhouse-nnue-v2-productive-container-v1.json`, 6,981 bytes,
SHA-256
`19c83c55c4c6bbb69bcf9acf77d3ac2eafc01ce2a18e3e20d10c6e084a9f5b9b`.

### Deterministic trainer kernel

The trainer operates on authenticated physical records and derives sparse rows
through an implementation independent of the C++ evaluator. A production run
must receive a canonical manifest that binds all chunks, roles, split audit,
teacher, provenance, source, code, and configuration and explicitly states
`training_admissible=true`. The output directory is exclusive and writes are
transactional.

A checkpoint binds source commit/tree, Python/PyTorch/Numpy versions, CPU-only
device and thread count, deterministic-runtime settings, architecture and
quantization identities, exact dataset/config hashes, model tensors,
optimizer tensors, RNG states, sample cursor, sample-order chain, and metrics.
Resume rejects any mismatch before creating output. The formal test compares
one uninterrupted run with an interruption inside an epoch followed by
resume: model and optimizer tensors, cursor, RNG, metrics stream, sample-order
chain, and final quantized container must match exactly.

The engineering micro-fit uses all 42 authenticated physical golden states but
does not read their teacher, result, terminal, or move labels. Its target is a
deterministic scalar derived from the position-identity hash under an explicit
engineering-only domain. AdamW and mean squared error are used only to exercise
nontrivial optimizer/resume state. Their settings receive no production or
model-selection credit. A production loss, calibration, optimizer recipe,
seeds, and training schedule require a later preregistration over admitted
data.

## Frozen verification contract

Before implementation results are observed, this checkpoint requires:

1. an expected-red dedicated-target run before productive implementation;
2. schema, descriptor, tensor layout, byte counts, and all identity pins to
   resolve mechanically;
3. independent Python serialize/parse/reserialize identity for a quantized
   artifact and exact C++ scalar inference over both perspectives;
4. layer-by-layer parity for transformer accumulators, clipped transformer
   outputs, both dense layers, raw output, and centipawn output;
5. adversarial parser mutations for every header class, tensor directory,
   identity, checksum, payload, reserved region, and arithmetic bound;
6. exporter rejection of wrong shapes, NaN/infinity, and every integer type
   overflow without leaving an artifact;
7. byte-identical final container and semantic checkpoint equality between an
   uninterrupted run and an interrupted/resumed run;
8. a changed dataset, config, source, environment, architecture, optimizer,
   RNG, or cursor must fail before resume output is created;
9. warning-clean release and assertion-enabled clean-export builds; and
10. the normal engine must remain exact legacy V1, use the registered
    `8ebf8478...` network, produce a real bestmove and contain no productive V2
    container identity.

An admitted sanitizer runtime remains separately required. Productive SIMD
and productive incremental/full-refresh parity remain separately required by
the CH-265 scope correction.

## Explicit non-claims

This ADR does not admit the golden corpus for training, select a model from
loss, begin a production training campaign, choose a champion, prove SIMD or
incremental integration, establish speed or Elo, authorize OpenBench, change
the default evaluator, or support release. G12 remains open after a successful
scalar/trainer checkpoint.

