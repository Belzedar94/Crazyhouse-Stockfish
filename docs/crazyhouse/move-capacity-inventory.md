# Crazyhouse move-capacity integration inventory

- Status: generator, MovePicker, reduction and typed ruleset selection complete; variant-specific MultiPV boundary pending
- Date: 2026-08-13
- Evidence class: `E1_ENGINEERING`
- Source: official Stockfish `5062aee519a1ba262d472d8ab139851ced56573e`
- Governing decision: ADR-0001, Decision 3
- Consultative review: receipt `p3-official-port-oracle-pro-manual-end-018.json`

## Boundary

The committed 303-legal-move fixture disproves `MAX_MOVES == 256` as a Crazyhouse capacity. It does not establish a replacement maximum. Crazyhouse storage therefore grows to the requested `size_t` capacity or aborts on checked arithmetic/allocation failure; it never truncates. `512` is only a reserve hint.

The chess path retains the official fixed storage and deterministic signature. The Crazyhouse path may share templated generation logic, but it cannot call an unchecked raw-pointer producer. No rule behavior or DROP generation may be enabled until every row below has a non-truncating path.

## Authenticated source surfaces

| Surface | Official blob | Current coupling | Required Crazyhouse treatment |
| --- | --- | --- | --- |
| `src/types.h` | `f51bdb89f5116dfa0262ae44ac33df5fe4197dfc` | `MAX_MOVES = 256` | Keep as the chess inline capacity; never reinterpret it as a Crazyhouse bound. |
| `src/movegen.h` | `ee79ea27ee4da69856e8ce1203966c078e48e7e0` | `generate(Position&, Move*)`; `MoveList` owns `Move[256]` | Keep the exact fixed sink for chess. Add a checked append sink and ruleset-selected storage for Crazyhouse. Both sinks must use one mechanically shared generator body. |
| `src/movepick.h` | `34c9fb30ecc91629ab956bb9bcd750363093ec57` | Each live picker owns `ExtMove[256]` and pointer stage boundaries | Keep the chess member/path. Each Crazyhouse picker owns its own growable `ExtMove` buffer and all stage boundaries are indices reacquired after growth. |
| `src/movepick.cpp` | `b97508a1b33f2f1e24782439ca13c227e62c514d` | Captures, bad captures, quiets and evasions share the fixed array; scoring writes through `*it++` | Checked append only for Crazyhouse. Capture/quiet partitions, sorting and selection use `[begin,end)` indices; appending quiets cannot invalidate an earlier capture boundary. |
| `src/thread.cpp` | `e34d321c3265cdf06e48beeaa497391f706152eb` | Root moves are copied from `MoveList<LEGAL>` into `std::vector` | The root vector is already growable; its producer must be the complete Crazyhouse legal list. Searchmoves filtering cannot silently substitute a capped list. |
| `src/search.h` | `a64b1f34318fb45268013c72588093575865ada7` | `reductions[MAX_MOVES]` is indexed by move number | Preserve the chess lookup table. Crazyhouse move numbers at or above 256 use the same frozen reduction formula through a checked accessor; no array index may depend on legal-list capacity. |
| `src/engine.cpp` | `c4dac7611d8a9d1d5d9cee03e76f11f898271321` | `MultiPV` option maximum equals `MAX_MOVES` accidentally | Decouple the protocol range from storage. The requested value is clamped to the dynamic root-vector size; no unproved Crazyhouse legal-move ceiling is introduced. Chess keeps its current option contract. |
| `src/perft.h` | `2c18c8b814d3a02b4f1db46772aa52afbe128f79` | Recursive and leaf perft consume `MoveList<LEGAL>` | Every ply receives a distinct ruleset-selected list; 303/512/1024 nested storage tests precede rule perft. |
| `src/uci.cpp` | `f908dbe8e1bff89b868104af73cc16bf70ceb832` | UCI replay matches text by enumerating `MoveList<LEGAL>` | Match against the complete growable list. A legal move beyond ordinal 255 must round-trip and replay. |
| `src/search.cpp` | `bc9c6303eb9e996ae6f70c84df955d436fa04af9` | Legality assertions, terminal helpers and skill/root loops consume list size or move number | All list consumers inherit the selected complete storage. Qsearch/evasions use the same per-live picker ownership. Move-count math uses the checked reduction accessor. |
| `src/position.cpp` | `a8585bc2f0c66a6af1398a1400f61feeab056d14` | `pseudo_legal`, mate/draw helpers and result logic create fixed `MoveList`s | Route every call through the complete list after ruleset state exists; no terminal predicate may treat truncation as no legal move. |
| `src/syzygy/tbprobe.cpp` | `24dda0d7e77780e5ad1827ee2b6065d084972d48` | Orthodox probes also construct fixed legal lists | The entire Syzygy entry boundary is disabled for Crazyhouse before these functions. Tests must prove zero probe entry; this file is not adapted as a Crazyhouse referee. |

## Producer contract

Generation is refactored mechanically before drops exist:

1. A fixed chess sink preserves `*end++` semantics inside the proven official 256-entry contract.
2. A Crazyhouse sink exposes only checked `push_back` into `CrazyhouseMoveBuffer<Move, 256>`.
3. The templated board generator accepts a sink; chess and Crazyhouse call the same board-move logic.
4. Crazyhouse DROP generation later appends through the same checked sink.
5. Debug builds reject duplicate raw encodings after complete generation.

The behavior-neutral sink refactor must reproduce the exact chess UCI inventory and deterministic bench signature before the DROP slice starts.

## Implementation checkpoint

Commit `2a2601e92ebc60ec940475de4a74626bdc8d3aaf` implements the mechanically shared checked generation body while preserving the official fixed sink and adding the forced-growable control path. The standalone buffer passed 303/512/1024, nested ownership, AddressSanitizer and GCC undefined-behavior-trap controls. Exact clean-export builds then passed without diagnostics.

The fixed binary from lease 050 and the forced-growable binary from lease 051 each advertised the same 19 ordered Stockfish options, no variant option, and produced 2,884,956 nodes plus 51 ordered bestmoves in three fresh `bench 16 1 13 default depth` processes. Every run matched frozen signature `78751f6d2a1146c15dac46875b52c4548deb3ca87475f478eebef5906f2d9259`. Receipts `p3-move-sink-dual-runtime-t1-end-050.json` and `p3-move-sink-growable-runtime-t1-end-051.json` keep timing and Crazyhouse claims explicitly out of scope.

Commit `95a043ce61a46e84639f326a36f0edb68c6913ed` replaces retained MovePicker pointers with indices and owns exactly one fixed or growable storage alternative. Lease 052 passed fixed selection, 303/512/1024 growth after a frozen capture boundary, partition/sort/uniqueness, nested ownership and both overflow controls. After rejecting one incorrectly flagged build under CH-061, leases 053-055 authenticated warning-strict fixed and combined-growable builds. Six fresh benches reproduced the same 19 options, 2,884,956 nodes, 51 bestmoves and `78751f6d…` signature.

Commit `06e76a1b82791bd381390be43500e377d9dabd30` preserves Stockfish's fixed reduction lookup through ordinal 255 and applies the same frozen formula through a checked accessor beyond it. Goldens at 256/303/512/1024, a nonpositive-index abort, strict clean builds and six fresh deterministic benches passed in leases 056-058.

Commit `7679f8a1fa7cc2a9cdfb190310c0be5171d2ca39` adds the frozen two-value `Ruleset` owner and makes every `MoveList` and live `MovePicker` select fixed storage for chess or checked growable storage for Crazyhouse. Root-position and trace copies preserve the ruleset, and the display-time Syzygy entry is explicitly chess-only. Lease 060 passed the exact parser/ABI/ownership fixture, two invalid-value aborts and warning-strict syntax over eight material consumers. Leases 061-062 then authenticated two 141/141-blob clean exports, linked both default and forced-growable controls, and reproduced the frozen 19-option inventory plus all six deterministic standard benches.

This checkpoint still does not admit DROP generation. The variant-specific MultiPV option range remains coupled to 256 and will stay unexposed until transactional UCI ruleset switching exists; complete Crazyhouse root storage is already growable and therefore no 256-move behavioral bound is accepted.

## Ownership and invalidation contract

- Every temporary `MoveList` owns its selected storage for its complete lifetime.
- Every live `MovePicker`, including recursive parent and child search nodes, owns distinct selected storage.
- No pointer, iterator or reference survives a Crazyhouse append that can grow the buffer.
- Picker stage state is stored as integer indices. Pointers are derived only for a bounded algorithm call after the relevant appends are complete.
- Root storage remains an owning vector copied from the complete root list.
- Perft and UCI replay create independent list owners; no static/thread-local spill buffer exists.

## Gate corpus

The capacity-only corpus requires:

- 303, 512 and 1024 unique sentinels;
- contiguous iteration, scoring, partition and sort integrity;
- `reserve(512)` followed by 1024 appends;
- nested parent/child spill with parent address and digest stability;
- explicit arithmetic-overflow failure without fallback;
- AddressSanitizer with a separate heap-overflow positive control;
- undefined-behavior trap instrumentation with a separate signed-overflow positive control;
- exact official chess build/UCI/options/three-run bench control.

After integration, the first rule-aware fixtures additionally place a legal UCI replay target above index 255, force capture/quiet stage growth, run nested perft, and prove Crazyhouse never enters Syzygy.

Atomic-Stockfish and Horde-Stockfish provide useful specialization and gate patterns but no demonstrated greater-than-256 move-capacity solution. No capacity code or bound is inherited from either project.
