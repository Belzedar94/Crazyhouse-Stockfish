# Crazyhouse NNUE V2 large-architecture ablation roadmap

Status: engineering roadmap; no model, strength, default, or release claim.

The first implemented control is `CH-NNUE-V2-LARGE-K64G1-SFNNV16` from ADR
0023. It deliberately preserves the current Stockfish SFNNv16 transform and
dense trunk while replacing orthodox input semantics with physical Crazyhouse
state. Legacy V1 remains productive and default.

## Central architectural question

An own-king-conditioned transformer is a strong orthodox-chess prior, but a
piece in hand has two distinct values in Crazyhouse:

- defensive value relative to its owner's king;
- offensive checking and mating value relative to the opponent's king.

The A0 control spends all 768 conditioned lanes on the own king. Its global
G1 lanes can learn interactions with the opponent king only through the dense
trunk. The highest-priority architecture ablation therefore reallocates the
same parameter budget between own-king and opponent-king conditioning.

## Frozen ablation order

### A0: own-K768 plus G256 control

- K rows: board, cumulative pockets, and promoted provenance conditioned on
  the oriented own-king square;
- G rows: the same physical facts without king conditioning;
- widths: K768 plus G256;
- purpose: validate the physical decoder, quantized datapath, incremental
  machinery, and the unmodified SFNNv16 trunk.

### A1: dual-K384 plus G256 at equal transformer weight count

- split the 768 conditioned lanes into 384 lanes bucketed by the own king and
  384 lanes bucketed by the opponent king;
- retain G256, the SFNNv16 transform, dense trunk, data, labels, quantizer,
  seeds, and all training settings;
- because `2 * 81,664 * 384 == 81,664 * 768`, conditioned transformer weight
  count is exactly unchanged;
- hypothesis: opponent-king conditioning exposes the checking and mating value
  of pockets directly, while own-king conditioning retains defensive reserve
  value.

This is the first model-selection ablation after A0 engineering. It must not be
combined with new threat rows or a topology change.

### A2: Crazyhouse virtual-drop threats

Starting from the winner of A0/A1, add derived sparse rows for attacks that a
currently held piece could create if dropped onto an empty square. Separate
sub-rungs are required:

1. checking-drop destinations by relative owner, piece type, and destination;
2. king-zone drop attacks and interpositions;
3. non-check tactical drop attacks.

Each sub-rung changes only feature content. Runtime and trainer must derive the
same rows from physical board, pockets, side to move, and check state. Legal
drop restrictions and pawn-rank restrictions are part of the goldens. These
rows are never stored as canonical DATAGEN records.

### A3: Crazyhouse FullThreats analogue

Adapt Stockfish's threat-domain idea to physical Crazyhouse instead of copying
its orthodox indices. Candidate content includes:

- board attacks and defenders;
- attacks after a legal checking drop;
- king-flight denial by board pieces and virtual drops;
- promoted-origin victim identity, because capturing it returns a pawn.

Board-only threats, virtual-drop threats, and promoted-victim content are
separate rungs. A single combined feature dump is not an admissible ablation.

### A4: phase and expert routing

The A0 control selects one of eight SFNNv16 trunks with
`min(7, total_pocket_units / 4)`. Compare, one at a time:

- total pocket units;
- weighted pocket material;
- available checking-drop count;
- a two-axis pocket/board phase collapsed by a frozen lookup table.

Only routing changes in these tests; features and trunk parameters remain
frozen. A learned gate is out of scope until deterministic integer routing is
specified and independently reproduced.

### A5: dense topology

Only after the input and routing ablations settle, compare larger or residual
dense trunks against the SFNNv16 control. Parameter count and inference cost
must be reported, but promotion is decided by equal-time Elo at the frozen time
controls rather than a speed threshold.

## Experimental controls

Every model-selection rung uses:

- the same authenticated physical dataset and trajectory-disjoint split;
- the same label perspective and teacher identity;
- the same quantized integer container as the actual tested artifact;
- multiple paired seeds with frozen seed identities;
- trainer/C++ row and layer parity before training admission;
- fixed-work and fixed-node checks before equal-time strength;
- VSTC and STC against the current accepted model baseline, followed by LTC
  only for the eventual Fairy-Stockfish comparison;
- no selection by training loss.

Canaries prove plumbing only. A0, A1, or any later rung does not replace Legacy
V1 until the quantized artifact wins all engineering and strength gates.
