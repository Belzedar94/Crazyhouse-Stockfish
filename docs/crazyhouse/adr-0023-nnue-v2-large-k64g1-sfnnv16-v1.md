# ADR 0023: Crazyhouse NNUE V2 Large K64G1 on the SFNNv16 trunk

- Status: accepted and frozen before implementation
- Date: 2026-08-28
- Evidence class: `E1_ENGINEERING`
- Rule profile: `LICHESS_CRAZYHOUSE_2026_08_12`
- Official Stockfish development anchor: `2edd935bbb3ea6e484a1700f582a95e0ee773ec2`
- Crazyhouse replay anchor before this ADR: `25bb7b2088ea329b6c5aa7e0ca482374066bdcb0`
- Product evaluator status: Legacy Crazyhouse V1 remains the only default
- External advisory review: waived by the owner; no API or credit fallback was used

## Context

The first productive V2 prototype proved a physical decoder, a scalar feature
inventory, integer loading, trainer/runtime parity and incremental update
methods. It deliberately used a small generic `902 -> 512` feature transformer
and a `1024 -> 32 -> 32 -> 1` CReLU trunk. That checkpoint remains valid
engineering evidence, but it is not the architecture selected for the current
Stockfish development line and has never displaced Legacy V1.

The current official Stockfish development anchor uses SFNNv16:

- `HalfKAv2_hm`: `22,528` rows;
- `FullThreats`: `59,808` rows;
- `PP_3Wide`: `4,560` rows;
- logical input total: `86,896` rows;
- a 1,024-lane feature-transformer accumulator;
- pair-product transformation to 512 bytes per perspective;
- side-to-move plus opponent concatenation to 1,024 bytes;
- eight `1024 -> 32 -> 32 -> 1` trunks with squared and clipped activations.

Crazyhouse cannot reuse those feature semantics unchanged. Pockets and
promoted-origin provenance are physical state, captures can change a visible
piece into a pawn in hand, and material leaves and re-enters the board. This ADR
keeps the current SFNNv16 transformation and trunk as the control while
replacing its orthodox input semantics and material bucket with Crazyhouse
contracts.

## Decision

The first current-development V2 architecture is
`CH-NNUE-V2-LARGE-K64G1-SFNNV16`.

It has two separately accumulated sparse domains for each perspective:

1. `K64`, a 64-own-king-square-conditioned domain with 81,664 rows and 768
   output lanes;
2. `G1`, a global unconditioned domain with 1,340 rows and 256 output lanes.

Both domains encode the same physical facts at different conditioning scales:
board occupancy, cumulative pocket slots, and visible promoted provenance.
They do not consume evaluator feature rows as canonical data. The canonical
input remains `crazyhouse-physical-v1` or a live `Position` proven equivalent
to that physical state.

### Perspective and orientation

For perspective `c`:

```text
orient_c(square) = square          when c is White
                   square XOR 56   when c is Black

relative_owner(c, owner) = 0       when owner == c
                           1       otherwise

king_bucket(c) = orient_c(own king square)  // 0..63
```

Only rank reflection plus color swap is admitted. File reflection and rotation
remain forbidden. White/Black share each weight table; perspective does not
multiply the serialized row count.

### Frozen orders and physical bounds

```text
droppable type order = P, N, B, R, Q
visible type order   = P, N, B, R, Q, K
promoted type order  = N, B, R, Q
pocket maxima        = 16, 4, 4, 4, 2
pocket prefixes      = 0, 16, 20, 24, 28
```

A count `n` activates pocket slots `[0,n)`. An empty pocket activates no pocket
row. This cumulative representation makes a drop or capture change exactly one
pocket row and replaces the old exact-count one-hot bands.

### K64 rows

Board planes are own/opponent pairs for `P,N,B,R,Q`, followed by one shared
king plane. The own king remains distinguishable because its oriented square
is the conditioning bucket.

```text
planeK(piece) = 2 * type5(piece) + relative_owner(piece)  // non-kings 0..9
                10                                        // either king

K_board = ((king_bucket * 11 + planeK) * 64) + orient(square)
range   = 0..45,055
rows    = 64 * 11 * 64 = 45,056
```

For pocket slot `j`:

```text
pocket_plane = relative_owner * 30 + pocket_prefix[type] + j  // 0..59
K_pocket     = 45,056 + king_bucket * 60 + pocket_plane
range        = 45,056..48,895
rows         = 64 * 60 = 3,840
```

Promoted provenance retains visible owner and visible type:

```text
promoted_plane = relative_owner * 4 + promoted_type4  // 0..7
K_promoted     = 48,896 + ((king_bucket * 8 + promoted_plane) * 64)
                 + orient(square)
range          = 48,896..81,663
rows           = 64 * 8 * 64 = 32,768
```

Total K64 rows: `45,056 + 3,840 + 32,768 = 81,664`.

### G1 rows

G1 has no king conditioning, so both kings retain relative ownership:

```text
planeG        = 2 * type6(piece) + relative_owner(piece)  // 0..11
G_board       = planeG * 64 + orient(square)
range         = 0..767
rows          = 12 * 64 = 768

G_pocket      = 768 + pocket_plane
range         = 768..827
rows          = 60

G_promoted    = 828 + promoted_plane * 64 + orient(square)
range         = 828..1,339
rows          = 8 * 64 = 512
```

Total G1 rows: `768 + 60 + 512 = 1,340`.

All feature indices use an unsigned 32-bit type. K64's maximum index does not
fit in 16 bits.

### Active capacity proof

Let `B` be board pieces, `P` total pocket units, `R` promoted-mask population,
and `U` physical units. Crazyhouse admission proves:

```text
U = B + P <= 32
R <= pawn-origin units <= 16
```

Each domain activates `B + P + R`, therefore:

```text
K64 max active per perspective = 48
G1  max active per perspective = 48
combined list per perspective  = 96
both perspectives              = 192 incidences
```

`48` is a per-domain, per-perspective limit, not a combined K+G capacity.

### Feature transformer and SFNNv16 transformation

```text
K64 weights: 81,664 * 768, int16
G1 weights:   1,340 * 256, int16
K64 bias:       768, int16
G1 bias:        256, int16
committed accumulators: int32
```

The first implementation uses int32 accumulators fail-closed. With arbitrary
serialized int16 values, a conservative lane bound is
`abs(bias) + 48 * 32,768 <= 1,605,632`; int16 accumulation is not admitted.

Each perspective is transformed exactly with the SFNNv16 pair-product scale:

```text
Kpair[j] = floor(clamp(K[j], 0, 255)
                 * clamp(K[j + 384], 0, 255) / 512)  // 384 bytes

Gpair[j] = floor(clamp(G[j], 0, 255)
                 * clamp(G[j + 128], 0, 255) / 512)  // 128 bytes

H[perspective] = concat(Kpair, Gpair)                 // 512 bytes
X = concat(H[side-to-move], H[opponent])              // 1,024 bytes
```

The `768/256` split is an architecture decision: 75 percent of accumulator
lanes are reserved for the king-conditioned domain while the global domain
retains 25 percent. Width changes are separate model-selection ablations.

### Dense trunk and buckets

Eight trunks preserve the current SFNNv16 structure:

```text
fc0: 1024 -> 32
concat(SqrClippedReLU(fc0), ClippedReLU(fc0)): 64
fc1: 64 -> 32
concat(SqrClippedReLU(fc1), ClippedReLU(fc1)): 64
fc2: concat(fc0 activations, fc1 activations) 128 -> 1
output += fc0[30] - fc0[31]
```

The Crazyhouse bucket is derived from total pocket units rather than orthodox
board material:

```text
bucket = min(7, total_pocket_units / 4)
```

The first rung has no PSQT head, `FullThreats`, `PP_3Wide`, alternate CReLU,
horizontal king mirroring, or additional rule/history rows. Each is a separate
ablation after the control passes engineering.

### Parameter and byte budget

```text
feature-transformer parameters including biases = 63,062,016
eight dense trunks including biases             =    280,072
total parameters                                = 63,342,088

feature-transformer integer bytes = 126,124,032
dense-trunk integer bytes         =     281,632
total tensor bytes                = 126,405,664 (about 120.55 MiB)
```

Container framing, tensor hashes, quantizer identity and provenance add bytes
and are frozen in later container/quantization contracts. A `.nnue` extension
alone never establishes compatibility.

## Incremental contract

K64 and G1 maintain separate immutable accumulator frames for both
perspectives. Slot changes are one-row deltas. Promoted provenance deltas carry
explicit owner, visible type and square; XOR of the promoted mask is
insufficient because a promoted-on-promoted capture can leave the destination
bit set while its owner/type changes.

Required transitions include quiet moves, drops, normal and promoted captures,
promoted-on-promoted captures, promotion with and without capture, en-passant,
castling, null, make/undo and repeated refresh/update interleavings.

- Own-king movement fully refreshes K64 for that perspective, at most 48 rows.
- G1 and the opponent's K64 remain incremental for the same king move.
- Null changes no feature row and only swaps side-to-move ordering.
- Undo pops the complete immutable accumulator frame.
- The largest non-refresh delta per domain is four removals and three additions.

Every transition must prove `incremental == scalar full refresh` for both
perspectives before SIMD or training admission.

## Engineering and scientific gates

Before training or model selection:

1. freeze feature and architecture schemas and hash-pin all implementation
   identities;
2. implement the physical decoder projection and scalar full refresh first;
3. prove C++/trainer index parity on frozen goldens and adversarial states;
4. implement a fail-closed versioned container with tensor dimensions, byte
   lengths, hashes, quantizer identity and complete provenance;
5. prove negative loads for legacy, wrong magic/version/schema, truncation,
   corruption, incompatible dimensions and trailing bytes with no fallback;
6. prove integer scalar parity, then SIMD/scalar parity;
7. prove incremental/full parity through all listed transitions and undo;
8. pass Legacy V1 through the new evaluator routing as a control;
9. authenticate deterministic resume across dataset, split, configuration,
   code, RNG and optimizer state;
10. select the actual quantized artifact by frozen validation and strength
    gates, never by training loss alone.

Canaries prove plumbing only. Equal-work, fixed-node, speed and equal-time
strength remain distinct evidence classes. Legacy V1 stays productive/default
until a quantized V2 champion passes the complete three-time-control gate.

## Supersession boundary

This ADR supersedes the old `902 -> 512` productive topology only as the design
target for the current Stockfish development line. It does not rewrite or
invalidate the old decoder, parser, parity or incremental-method receipts. It
does not claim a trained model, Elo, OpenBench result, default change or
release.
