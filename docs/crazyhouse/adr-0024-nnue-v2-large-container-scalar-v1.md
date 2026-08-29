# ADR 0024: owned large-V2 container and scalar SFNNv16 contract

- Status: accepted and implemented as an engineering-only A0 checkpoint
- Date: 2026-08-29
- Evidence class: `E1_ENGINEERING`
- Architecture: `CH-NNUE-V2-LARGE-K64G1-SFNNV16`
- Product evaluator: Legacy Crazyhouse V1 remains the only default
- External advisory review: waived by the owner

## Decision

The large A0 evaluator uses an owned, fixed-size, little-endian container. It
does not accept an orthodox Stockfish network, a legacy Crazyhouse network, the
old 902-row V2 prototype, an unknown extension, trailing bytes or implicit
defaults. The exact file size is 126,406,688 bytes:

```text
header  =      1,024 bytes
tensors = 126,405,664 bytes
file    = 126,406,688 bytes
```

The magic is the 16 bytes `CHNNUEV2LARGEA0\0`, version is 1.0 and committed
flags equal one. The header contains exact dimensions, integer types,
activation constants, tensor directory, fixed semantic identities, complete
training provenance, a SHA-256 of the tensor payload and a CRC32C of the full
header with its CRC field zeroed.

## Semantic identities

The following SHA-256 inputs include the terminal newline and are frozen:

```text
feature input = "crazyhouse-v2-large-k64g1-feature-contract-v1\n"
feature sha   = 6e616c2e090b43daa7710ca39aaedc76b43a90db46e8f093466f45b821f44a79

architecture input = "CH-NNUE-V2-LARGE-K64G1-SFNNV16\n"
architecture sha   = 2f5efc7cf05f3365bf5e524e636d47a6abdbadcdf5673cc0d260f1e61638341e

quantization input = "crazyhouse-v2-large-sfnnv16-quantization-v1\n"
quantization sha   = 262399c3d1e8f96681f485d8b2d9d6d1c8e783cd1685250317a9c7e244c9386c
```

The rule-profile and physical-schema identities remain respectively
`d0602bc32877639f2d9a70741614882512083431b48b9f4e98a88e1067eb4d68`
and `c72a1fac41e311ed09a2167c56887d64b18293149291f6505f4021f348c1ef55`.

Every load also requires six nonzero caller-supplied identities and exact
header equality: dataset manifest, trajectory-disjoint split manifest,
training configuration, trainer code, training runtime and resume lineage.
Failure exposes no partial network object.

## Header map

All integer fields are unsigned little-endian unless a tensor type says
otherwise.

| Offset | Bytes | Meaning |
|---:|---:|---|
| 0 | 16 | Magic |
| 16 | 4 | Byte-order marker `0x01020304` |
| 20 | 2 | Header bytes, 1,024 |
| 22 | 2 | Major version, 1 |
| 24 | 2 | Minor version, 0 |
| 26 | 2 | Committed flags, 1 |
| 28 | 4 | Exact file bytes |
| 32 | 4 | Exact payload bytes |
| 36 | 2 | Tensor count, 10 |
| 38 | 2 | Dense layer stacks, 8 |
| 40..88 | 13 x 4 | K/G rows, active cap, widths and dense dimensions |
| 92 | 4 | Pocket bucket divisor, 4 |
| 96 | 4 | Pocket bucket maximum, 7 |
| 100..114 | 8 x 2 | Tensor/accumulator/activation type identifiers |
| 116..172 | 15 x 4 | Transform, activation, output and directory constants |
| 176..223 | 48 | Reserved zero |
| 224..383 | 5 x 32 | Rule, physical, feature, architecture and quantization identities |
| 384..575 | 6 x 32 | Required training provenance identities |
| 576 | 32 | SHA-256 of bytes 1,024 through EOF |
| 608 | 4 | Header CRC32C with this field zeroed |
| 612..623 | 12 | Reserved zero |
| 624..1023 | 10 x 40 | Tensor directory |

Each directory entry contains tensor ID, element type, rank, flags, absolute
offset, byte count and four dimensions. The exact tensor layout is:

| ID | Tensor | Type | Shape | Offset | Bytes |
|---:|---|---|---|---:|---:|
| 1 | K64 weights | int16 | 81,664 x 768 | 1,024 | 125,435,904 |
| 2 | K64 bias | int16 | 768 | 125,436,928 | 1,536 |
| 3 | G1 weights | int16 | 1,340 x 256 | 125,438,464 | 686,080 |
| 4 | G1 bias | int16 | 256 | 126,124,544 | 512 |
| 5 | fc0 biases | int32 | 8 x 32 | 126,125,056 | 1,024 |
| 6 | fc0 weights | int8 | 8 x 32 x 1,024 | 126,126,080 | 262,144 |
| 7 | fc1 biases | int32 | 8 x 32 | 126,388,224 | 1,024 |
| 8 | fc1 weights | int8 | 8 x 32 x 64 | 126,389,248 | 16,384 |
| 9 | fc2 biases | int32 | 8 | 126,405,632 | 32 |
| 10 | fc2 weights | int8 | 8 x 128 | 126,405,664 | 1,024 |

Dense weights are serialized output-major inside each bucket. There is no
Stockfish weight permutation in this owned format.

## Exact scalar arithmetic

K64 and G1 use int16 weights/biases and committed int32 accumulators. The
active capacity makes every transformer sum fit int32 for arbitrary serialized
int16 values. Each perspective uses the ADR 0023 pair product:

```text
pair(a,b) = floor(clamp(a,0,255) * clamp(b,0,255) / 512)
```

The side-to-move perspective precedes its opponent. The selected trunk is:

```text
bucket = min(7, total_pocket_units / 4)
```

The evaluator authenticates that the declared pocket-unit total equals the
number of cumulative K64 and G1 pocket rows for both perspectives.

For `fc0` the linear shift is 7; for `fc1` it is 6:

```text
squared(v,s) = min(127, (int64(v) * v) >> (2*s + 7))
clipped(v,s) = clamp(v >> s, 0, 127)
```

The loader rejects a trunk whose possible fc0 or fc1 range for byte inputs
0..127 does not fit int16. This prevents scalar/SIMD disagreement at
Stockfish's saturating 32-to-16-bit activation boundary. The final affine
concatenates squared and clipped fc0 and fc1 activations, then applies the
Stockfish skip and scale exactly:

```text
fwd = fc2 + fc0[30] - fc0[31]
output = trunc_toward_zero(fwd * 9600 / 16384)
```

The loader rejects any fc2 interval that cannot remain int32 after the full
skip range.

## Verification and boundary

The C++ verifier is separate from the normal engine. An independent Python
writer/reference generates the complete 126,406,688-byte container, checks
six full scalar traces and exercises wrong size, provenance, header,
identities, directory, checksums, corruption and dense-interval failures.

This checkpoint is scalar and full-refresh only. It is not a trainer, trained
network, SIMD implementation, incremental accumulator, deterministic resume,
model-selection result, Elo result, default change, G12 closure or release
evidence.
