# ADR-0003: legacy Crazyhouse evaluator compatibility contract

- Status: parser contract frozen; numerical routing closed pending authenticated goldens
- Date: 2026-08-13
- Evidence class: `E1_ENGINEERING`
- Applies to: `LegacyCrazyhouseNetworkV1` only

## Decision context

Crazyhouse-Stockfish remains based exclusively on official Stockfish development commit `5062aee519a1ba262d472d8ab139851ced56573e`. Fairy-Stockfish is not a source base. Its pinned source objects and executable behavior may be used as GPL-compatible donor evidence and as a reproducible behavioral oracle, but neither establishes correctness alone. Atomic-Stockfish and Horde-Stockfish remain pattern references only.

The mandatory compatibility artifact is the 58,534,811-byte network with SHA-256 `8ebf84784ad20fa33df403e60211818a7486db7cb8c3decfc86a80238d254f43`. Its pinned Lichess asset declaration is CC0. A candidate public name such as `Crazyhouse_v1.nnue` is an alias only when its bytes have this exact identity.

The required pre-decision Oracle invocation used the ChatGPT Pro browser session `crazyhouse-legacy-evaluator-adr`. Its dry-run passed, but the live response stalled after the introductory sentence through two same-session recovery attempts. No recommendation was available, no API or credits were used, and no fallback was attempted. The immutable operational receipt is `receipts/private/p5-legacy-evaluator-oracle-stalled-025.json`. This ADR therefore freezes only independently inspected facts and reversible boundaries.

## Separate claims

The following claims never imply one another:

1. `PARSER_ACCEPTED`: the exact registered bytes decode completely into the frozen tensor layout.
2. `SCALAR_PARITY`: independently derived scalar full-refresh features and integer propagation match authenticated golden vectors exactly.
3. `INCREMENTAL_PARITY`: every make/undo/null/drop/capture/promotion transition matches scalar full refresh.
4. `SEARCH_CORRECT`: transactional UCI routing, worker state, search, clocks and result handling pass their engineering corpus.
5. `STRENGTH`: preregistered equal-time tests support a playing-strength claim.

G5 cannot pass on header inspection or parser acceptance alone. Crazyhouse search stays unavailable until at least `PARSER_ACCEPTED` and `SCALAR_PARITY` pass, with explicit engine-level routing tests.

## Registered container identity

All integer fields are little-endian. The one admitted V1 artifact has this exact layout:

| Offset | Bytes | Field |
| ---: | ---: | --- |
| 0 | 4 | file version `0x7af32f20` |
| 4 | 4 | network hash `0x3c103e72` |
| 8 | 4 | description length `75` |
| 12 | 75 | UTF-8 description `Network trained with the https://github.com/glinscott/nnue-pytorch trainer.` |
| 87 | 4 | transformer hash `0x5f2348b8` |
| 91 | 1,024 | 512 signed 16-bit transformer biases |
| 1,115 | 56,623,104 | 55,296 x 512 signed 16-bit transformer weights |
| 56,624,219 | 1,769,472 | 55,296 x 8 signed 32-bit PSQT weights |
| 58,393,691 | 141,120 | eight 17,640-byte layer stacks |
| 58,534,811 | 0 | mandatory EOF |

Each layer stack starts with architecture hash `0x633376ca`, followed by:

| Tensor | Bias bytes | Weight bytes | Serialized input x output |
| --- | ---: | ---: | ---: |
| affine 0 | 64 | 16,384 | 1,024 x 16 |
| affine 1 | 128 | 1,024 | 32 padded inputs x 32 outputs |
| affine 2 | 4 | 32 | 32 x 1 |

The eight architecture hashes were observed at offsets `58,393,691`, `58,411,331`, `58,428,971`, `58,446,611`, `58,464,251`, `58,481,891`, `58,499,531`, and `58,517,171`. The byte equation is exact:

```text
87
+ 4 + 512*2 + 55296*512*2 + 55296*8*4
+ 8 * (4 + (16*4 + 1024*16) + (32*4 + 32*32) + (1*4 + 32*1))
= 58,534,811
```

## Fail-closed parser

`LegacyCrazyhouseNetworkV1` is a standalone backend. It does not reuse, reinterpret, subclass or populate official Stockfish's current `Network` object.

The loader performs these bounded stages:

1. reject a missing, non-readable or non-regular input;
2. inspect length before allocation and reject every byte shorter or longer than 58,534,811;
3. read exactly that many bytes under the same upper bound;
4. validate the fixed header and description before tensor allocation;
5. decode all tensors into a local candidate using explicit little-endian signed bit patterns;
6. validate all eight architecture hashes and exact EOF;
7. calculate SHA-256 and require the registered digest;
8. commit the candidate as loaded only after every check succeeds.

Every load attempt begins by invalidating the prior backend. Failure leaves no earlier or partial network usable. Diagnostics distinguish missing/read, truncated, oversized, version, network hash, description length/content, transformer hash, architecture hash, tensor/layout/EOF and registered-digest failures. Filename and `.nnue` extension never establish identity. There is no classical, current-NNUE or previously loaded fallback.

## Independently derived feature domain

For the frozen 8x8 Crazyhouse profile, piece-type order is pawn, knight, bishop, rook, queen, king, with the king family last. Let `t` be that zero-based type ordinal, `p` the evaluator perspective, `c` the piece owner, and `relative(p, s)` be `s` for White and a rank flip for Black.

- There are 11 board planes: two relative-color planes for each non-king type and one shared-color king plane. Their size is `11 * 64 = 704` per king square.
- There are ten pocket bands: two relative-color bands for each droppable type. Each band reserves 16 cumulative count slots, for `10 * 16 = 160` features per king square.
- The per-king stride is `704 + 160 = 864`.
- The own-king bucket count is 64, producing `64 * 864 = 55,296` transformer features.

Board feature index:

```text
king = relative(p, own_king_square) * 864
plane = (2*t + ((t != king_type) && (c != p))) * 64
index = king + plane + relative(p, piece_square)
```

Pocket feature indices are cumulative. For non-king `t`, the `n`th held piece activates slots `0..n-1`:

```text
king = relative(p, own_king_square) * 864
band = 704 + (2*t + (c != p)) * 16
index(slot) = king + band + slot
```

The admitted physical limits guarantee that no active slot reaches 16. Duplicate pieces in a pocket do not create duplicate indices; they activate successive count slots. Active feature lists must be canonical, in range and duplicate-free for each perspective.

The correct layer bucket uses board occupancy only, not pockets:

```text
bucket = min(((board_piece_count - 1) * 8) / 32, 7)
```

All divisions are integer divisions. Every admitted position has both kings and a total physical inventory bounded by the frozen rule profile.

## Promoted-origin provenance

Promoted-origin markers remain mandatory physical rule state, affect capture-to-pocket transitions, hashing, repetition, serialization and labels, and must survive exact make/undo. V1 intentionally has no promoted-origin input plane. Two states with identical side to move, current board piece identities and squares, and pockets but different valid promoted-origin markers therefore have identical V1 active features and raw V1 output.

This is a compatibility invariant, not a claim that provenance is unimportant. The future physical dataset and NNUE V2 must retain and represent provenance. V1 bytes must never be reinterpreted with invented provenance rows.

## Scalar integer contract

Scalar full refresh is implemented before any incremental or SIMD path:

- Transformer accumulators start at signed 16-bit biases and add active signed 16-bit weights with explicit two's-complement modulo-`2^16` behavior.
- PSQT accumulators start at zero and add signed 32-bit weights with explicit modulo-`2^32` behavior.
- The transformed input orders side-to-move perspective first and the opposite perspective second, with each signed 16-bit accumulator clamped to `[0,127]` and converted to unsigned 8-bit.
- The raw PSQT component is computed by subtracting the opposite-perspective accumulator bits from the side-to-move accumulator bits modulo `2^32`, reinterpreting the result as signed 32-bit, and dividing once by two with C++ truncation toward zero. Dividing the two perspective values separately is not equivalent and is forbidden.
- Affine tensors are serialized as canonical output-major rows. For output `o`, padded input width `P` and input `i`, the file offset is `o * P + i`. Donor SSSE3 permutations are private runtime layout transforms and never alter the parser's canonical arrays.
- Affine biases are signed 32-bit and weights signed 8-bit. Affine 0 uses 1,024 real inputs. Affine 1 serializes 32 inputs per output but consumes 16 real activations followed by 16 explicit zeroes. Affine 2 consumes 32 real inputs. Each signed product is added through an unsigned 32-bit representation modulo `2^32`, then bit-preservingly reinterpreted as signed 32-bit; native signed-overflow behavior is never relied upon.
- Affine 0 and 1 activations shift right by six and clamp to `[0,127]`. Since every negative result clamps to zero, the negative right-shift rounding choice cannot affect an activated value.
- Affine 2 emits the raw positional component.

The scalar entry point accepts a `Position`, invokes the certified V1 feature extractor internally, validates its cross-boundary invariants, and returns no output on any failure. It does not accept caller-supplied indices or buckets and does not touch official Stockfish NNUE accumulators. Loading and evaluation on the same network object are not concurrent operations until a separate synchronization design is admitted.

The raw pair `{psqt, positional}` and all eight bucket pairs are the primary numerical parity surface. The legacy V1 value is separately computed with the frozen legacy blend and output scale 16. Any production adapter and any board-material scaling are explicit named layers with separate goldens; the current official Stockfish optimism, material, rule-50 and WDL transforms do not silently process V1 output.

## Legacy value and Crazyhouse outer adapter

The compatibility adapter derives its own board inventory from `Position`; it never reads current official-Stockfish piece values or an incrementally maintained material total. Pockets do not enter this adapter. The frozen donor middle-game values are knight `781`, bishop `825`, rook `1276` and queen `2538`; pawns and kings contribute zero to non-pawn material. The entertainment threshold is `825 - 781 = 44`.

For the selected raw pair `p = psqt`, `x = positional`, let `delta` be the absolute difference between the two board-only non-pawn material totals. The unadjusted value uses `e = 0`; the adjusted value uses `e = 7` exactly when `delta <= 44`, otherwise `e = 0`:

```text
A = 128 - e
B = 128 + e
blend_numerator = A * p + B * x
blend_sum = blend_numerator / 128
legacy_value = blend_sum / 16
```

Both divisions are distinct signed integer divisions with C++ truncation toward zero. The implementation uses wider intermediates only to detect whether the donor's signed 32-bit intermediate would overflow; such input fails closed rather than defining a new wrap contract.

For the Lichess Crazyhouse V1 profile only, let `P` be the number of pawns on the board and `N` the total frozen board-only non-pawn material across both colors:

```text
scale = 903 + 32 * P + (32 * N) / 1024
outer_pre_clamp = adjusted * scale / 1024
outer = clamp(outer_pre_clamp, -31507, 31507)
```

Each displayed division again truncates toward zero and occurs in that order. The final limits reproduce donor `VALUE_TB_LOSS_IN_MAX_PLY + 1` and `VALUE_TB_WIN_IN_MAX_PLY - 1`, with donor `VALUE_MATE = 32000` and `MAX_PLY = 246`. The adapter reports both the pre-clamp value and whether clamping occurred. Corpus observations do not prove that the clamp is unreachable globally, so it is modeled rather than assumed away. Chess960 correction, check-counting, classical hybrid selection, move-rule damping, optimism and WDL conversion are not part of this profile. A Chess960-tagged position fails closed.

## Golden-vector authority

Before scalar evaluation code is admitted, a golden corpus must bind all of the following:

- exact V1 network SHA-256;
- exact behavior-oracle executable SHA-256, source commit, clean-build recipe and UCI option inventory;
- rule profile, canonical FEN and side to move;
- ordered active indices and their digest for both perspectives;
- board-piece count and selected layer bucket;
- raw PSQT and positional values for every one of the eight buckets;
- selected-bucket legacy value and explicitly named outer-adapter value.

At least two independent paths must agree: an authenticated pinned legacy behavior oracle and a separately implemented scalar decoder derived from this document. The legacy executable is a behavioral reference, never source authority. Traces rounded for display are insufficient when an exact raw integer can be extracted from a reproducible instrumented reference build.

The corpus includes start positions for both sides to move; rank-flip/color-swap metamorphics; every pocket owner/type and successive-count boundary; each layer bucket boundary; drops, captures, en-passant, promotion and promoted capture; a provenance-only pair expected to collide in V1 features; and adversarial maximum admitted inventories.

## Admission order and stop conditions

1. Freeze parser fixture and observe the expected pre-implementation failure.
2. Implement the standalone parser and pass positive, alias, missing, truncated, oversized, incompatible-header, corrupt-parameter, wrong-architecture, trailing/layout and failure-invalidation cases.
3. Authenticate a clean behavior oracle and freeze exact golden vectors.
4. Implement feature enumeration only; compare ordered indices and digests.
5. Implement scalar full refresh only; require exact raw parity for every vector and bucket.
6. Add the explicit legacy value/adapter layer and its separate goldens.
7. Add transactional UCI/backend routing while keeping search disabled; prove wrong-route and replacement failures.
8. Enable a bounded search smoke only after the preceding gates pass.
9. Implement incremental state, require incremental equals full refresh after every variant transition and undo, and only then consider SIMD.

Stop immediately on any size arithmetic disagreement, out-of-range or duplicate feature, unproved perspective/order assumption, signed-arithmetic mismatch, rounded-only golden, provenance-dependent V1 output, failed load retaining a usable backend, crossed `EvalFile` route, or any fallback. Parser or scalar PASS remains `E1_ENGINEERING`; it is not Elo, model selection, strength or release evidence.
